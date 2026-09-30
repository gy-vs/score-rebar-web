"""SQLite-backed document store.

A document row is one confirmed ``Score`` (JSON) plus its list of pending
proposed edits (JSON). Confirmed writes are atomic with the version bump so
that GET, layout and export can never observe different revisions.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .models import Score

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    version INTEGER NOT NULL,
    data TEXT NOT NULL,
    proposals TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.Lock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return conn

    # ---------- reads ----------
    def get(self, score_id: str) -> Optional[Score]:
        with self._conn() as c:
            row = c.execute("SELECT data FROM scores WHERE id=?", (score_id,)).fetchone()
        return Score.from_dict(json.loads(row["data"])) if row else None

    def get_proposals(self, score_id: str) -> list[dict]:
        with self._conn() as c:
            row = c.execute("SELECT proposals FROM scores WHERE id=?", (score_id,)).fetchone()
        return json.loads(row["proposals"]) if row else []

    def list_documents(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id,title,version,updated_at FROM scores ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- writes ----------
    def save(self, score: Score, proposals: list[dict] | None = None) -> None:
        data = json.dumps(score.to_dict(), ensure_ascii=False)
        props = json.dumps(proposals if proposals is not None else [],
                           ensure_ascii=False)
        with self._lock, self._conn() as c:
            c.execute(
                """INSERT INTO scores(id,title,version,data,proposals,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title, version=excluded.version,
                     data=excluded.data, proposals=excluded.proposals,
                     updated_at=excluded.updated_at""",
                (score.id, score.title, score.version, data, props,
                 score.created_at, score.updated_at),
            )

    def add_proposal(self, score_id: str, proposal: dict) -> None:
        props = self.get_proposals(score_id)
        props.append(proposal)
        with self._lock, self._conn() as c:
            c.execute("UPDATE scores SET proposals=? WHERE id=?",
                      (json.dumps(props, ensure_ascii=False), score_id))

    def remove_proposal(self, score_id: str, proposal_id: str) -> list[dict]:
        props = [p for p in self.get_proposals(score_id) if p["id"] != proposal_id]
        with self._lock, self._conn() as c:
            c.execute("UPDATE scores SET proposals=? WHERE id=?",
                      (json.dumps(props, ensure_ascii=False), score_id))
        return props

    def create_from(self, score: Score) -> None:
        self.save(score, [])
