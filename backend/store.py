"""Persistent score store with confirmed-state versioning.

Every accepted edit produces a new immutable *revision*: the score model, its
display projection and the exported MusicXML are all derived from that exact
revision, so the browser can never see a layout from one state and an export
from another.  Revisions are persisted as JSON files on disk; reopening a
score restores the same time signatures, voices and edits without depending
on browser-only objects.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

from .edits import validate_score
from .model import Score
from . import notation


class RevisionMismatch(Exception):
    """Client held a stale revision; its edit was not applied."""


class NotFound(Exception):
    pass


class Store:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self._lock = threading.RLock()

    # ---- paths -------------------------------------------------------------

    def _dir(self, score_id: str) -> str:
        d = os.path.join(self.root, score_id)
        os.makedirs(d, exist_ok=True)
        return d

    def _rev_path(self, score_id: str, rev: str) -> str:
        return os.path.join(self._dir(score_id), f"{rev}.json")

    def _head_path(self, score_id: str) -> str:
        return os.path.join(self._dir(score_id), "HEAD")

    # ---- create / read -----------------------------------------------------

    def create(self, score: Score) -> str:
        validate_score(score)
        rev = self._write(score, base=None, label="import")
        self._set_head(score.score_id, rev)
        return rev

    def exists(self, score_id: str) -> bool:
        return os.path.exists(self._head_path(score_id))

    def list_scores(self) -> list[dict]:
        out = []
        if not os.path.isdir(self.root):
            return out
        for name in sorted(os.listdir(self.root)):
            hp = os.path.join(self.root, name, "HEAD")
            if os.path.exists(hp):
                rev = open(hp).read().strip()
                doc = self._read_rev(name, rev)
                out.append({"scoreId": name, "title": doc["title"],
                            "revision": rev,
                            "updatedAt": doc.get("updatedAt")})
        return out

    def head_revision(self, score_id: str) -> str:
        hp = self._head_path(score_id)
        if not os.path.exists(hp):
            raise NotFound(score_id)
        return open(hp).read().strip()

    def get_score(self, score_id: str, rev: str | None = None) -> tuple[Score, str]:
        rev = rev or self.head_revision(score_id)
        doc = self._read_rev(score_id, rev)
        return doc["score_model_obj"], rev

    def get_document(self, score_id: str, rev: str | None = None) -> dict:
        """Full confirmed document: score + projection, at one revision."""
        with self._lock:
            rev = rev or self.head_revision(score_id)
            doc = self._read_rev(score_id, rev)
        return doc

    def get_projection(self, score_id: str, rev: str | None = None) -> tuple[dict, str]:
        doc = self.get_document(score_id, rev)
        return doc["projection"], doc["revision"]

    def get_musicxml(self, score_id: str, rev: str | None = None) -> tuple[str, str]:
        from .musicxml_io import export_musicxml
        with self._lock:
            rev = rev or self.head_revision(score_id)
            doc = self._read_rev(score_id, rev)
            cached = doc.get("musicxml")
            if cached is None:
                cached = export_musicxml(doc["score_model_obj"])
                doc["musicxml"] = cached
                self._write_doc(score_id, rev, doc)
        return cached, rev

    # ---- mutation ----------------------------------------------------------

    def commit(self, score_id: str, base_rev: str, mutate, label: str) -> tuple[str, dict]:
        """Apply ``mutate(score)`` optimistically against ``base_rev``.

        Optimistic concurrency: if the client's ``base_rev`` is not the current
        head, :class:`RevisionMismatch` is raised and NOTHING is written, so a
        stale edit can't silently land on a newer state.  On success a new
        revision file is written atomically and HEAD advances.
        """
        from .musicxml_io import export_musicxml
        with self._lock:
            head = self.head_revision(score_id)
            if base_rev != head:
                raise RevisionMismatch(
                    f"base {base_rev} is stale; head is {head}")
            doc = self._read_rev(score_id, head)
            score = doc["score_model_obj"]
            result = mutate(score)
            validate_score(score)
            new_rev = self._write(score, base=head, label=label,
                                  result=result)
            self._set_head(score_id, new_rev)
            return new_rev, self._read_rev(score_id, new_rev)

    # ---- internals ---------------------------------------------------------

    def _write(self, score: Score, base: str | None, label: str,
               result: dict | None = None) -> str:
        rev = uuid.uuid4().hex[:12]
        projection = notation.project(score)
        from .musicxml_io import export_musicxml
        doc = {
            "revision": rev,
            "parent": base,
            "label": label,
            "updatedAt": time.time(),
            "title": score.title,
            "score": score.to_wire(),
            "projection": projection,
            "musicxml": export_musicxml(score),
            "editResult": result or {},
        }
        self._write_doc(score.score_id, rev, doc)
        return rev

    def _write_doc(self, score_id: str, rev: str, doc: dict) -> None:
        path = self._rev_path(score_id, rev)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f)
        os.replace(tmp, path)

    def _set_head(self, score_id: str, rev: str) -> None:
        hp = self._head_path(score_id)
        tmp = hp + ".tmp"
        with open(tmp, "w") as f:
            f.write(rev)
        os.replace(tmp, hp)

    def _read_rev(self, score_id: str, rev: str) -> dict:
        path = self._rev_path(score_id, rev)
        if not os.path.exists(path):
            raise NotFound(f"{score_id}@{rev}")
        with open(path) as f:
            doc = json.load(f)
        # Attach a live model object (not JSON-serialized into the cache file
        # permanently; regenerated on read).
        doc["score_model_obj"] = Score.from_wire(doc["score"])
        return doc
