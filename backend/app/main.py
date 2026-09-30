"""HTTP API: one confirmed state per document.

Every mutating request sends the client's known ``baseVersion``. Accepted
edits advance the version atomically; the very next GET/export therefore
observes the same rebarred structure — the client never renders a layout
for a revision the server has not confirmed.  Rejected edits are stored as
pending proposals attached to the document and returned unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import edits
from .layout import derive_layout
from .models import Score, new_id, seed_score
from .musicxml import export_musicxml, import_musicxml
from .storage import Store

DATA_DIR = Path(os.environ.get("SCORE_REBAR_DATA", Path(__file__).resolve().parent.parent / "data"))
STORE = Store(DATA_DIR / "scores.db")

app = FastAPI(title="score-rebar-web")


# ---------- response assembly ----------

def _score_payload(score: Score, proposals: list[dict]) -> dict:
    layout = derive_layout(score)
    return {
        "score": score.to_dict(),
        "layout": layout,
        "proposals": proposals,
    }


def _require(score_id: str) -> tuple[Score, list[dict]]:
    score = STORE.get(score_id)
    if score is None:
        raise HTTPException(404, f"文档 {score_id} 不存在")
    return score, STORE.get_proposals(score_id)


def _check_version(score: Score, base_version: int | None) -> None:
    if base_version is not None and base_version != score.version:
        raise HTTPException(
            409,
            f"版本过期：客户端基于 v{base_version}，服务端已是 v{score.version}，请先重新获取谱面",
        )


# ---------- models for requests ----------

class MeterReq(BaseModel):
    baseVersion: int | None = None
    fromMeasure: int = Field(ge=0)
    beats: int
    beatUnit: int


class PitchReq(BaseModel):
    step: str
    alter: int = 0
    octave: int


class PitchEditReq(BaseModel):
    baseVersion: int | None = None
    itemId: str
    pitches: list[PitchReq]


class DurationEditReq(BaseModel):
    baseVersion: int | None = None
    itemId: str
    duration: dict   # {'num','den'}


class MoveReq(BaseModel):
    baseVersion: int | None = None
    itemId: str
    onset: dict


class ProposalAction(BaseModel):
    proposalId: str


# ---------- routes ----------

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/scores")
def list_scores():
    return {"documents": STORE.list_documents()}


@app.post("/api/scores/seed")
def create_seed():
    score = seed_score()
    STORE.create_from(score)
    return _score_payload(score, [])


@app.get("/api/scores/{score_id}")
def get_score(score_id: str):
    score, proposals = _require(score_id)
    return _score_payload(score, proposals)


@app.post("/api/scores/{score_id}/meter")
def set_meter(score_id: str, req: MeterReq):
    score, proposals = _require(score_id)
    _check_version(score, req.baseVersion)
    try:
        edits.set_meter(score, req.fromMeasure, req.beats, req.beatUnit)
    except edits.EditError as e:
        return _reject(score_id, score, "set_meter", req.model_dump(), e)
    STORE.save(score, proposals)
    return _score_payload(score, proposals)


@app.post("/api/scores/{score_id}/pitch")
def edit_pitch(score_id: str, req: PitchEditReq):
    score, proposals = _require(score_id)
    _check_version(score, req.baseVersion)
    try:
        edits.change_pitch(score, req.itemId, [p.model_dump() for p in req.pitches])
    except edits.EditError as e:
        return _reject(score_id, score, "change_pitch", req.model_dump(), e)
    STORE.save(score, proposals)
    return _score_payload(score, proposals)


@app.post("/api/scores/{score_id}/duration")
def edit_duration(score_id: str, req: DurationEditReq):
    score, proposals = _require(score_id)
    _check_version(score, req.baseVersion)
    from .rational import as_q
    try:
        edits.change_duration(score, req.itemId, as_q(req.duration))
    except edits.EditError as e:
        return _reject(score_id, score, "change_duration", req.model_dump(), e)
    STORE.save(score, proposals)
    return _score_payload(score, proposals)


@app.post("/api/scores/{score_id}/move")
def move_item(score_id: str, req: MoveReq):
    score, proposals = _require(score_id)
    _check_version(score, req.baseVersion)
    from .rational import as_q
    try:
        edits.move_item(score, req.itemId, as_q(req.onset))
    except edits.EditError as e:
        return _reject(score_id, score, "move_item", req.model_dump(), e)
    STORE.save(score, proposals)
    return _score_payload(score, proposals)


@app.post("/api/scores/{score_id}/proposals/{proposal_id}/dismiss")
def dismiss_proposal(score_id: str, proposal_id: str):
    score, _ = _require(score_id)
    props = STORE.remove_proposal(score_id, proposal_id)
    return _score_payload(score, props)


@app.get("/api/scores/{score_id}/export")
def export_score(score_id: str):
    score, _ = _require(score_id)
    data = export_musicxml(score)
    from urllib.parse import quote
    safe_title = (score.title or score_id).replace("/", "_")
    ascii_name = score_id + ".musicxml"
    fname = f"attachment; filename=\"{ascii_name}\"; " \
            f"filename*=UTF-8''{quote(safe_title + '.musicxml')}"
    return Response(
        content=data, media_type="application/vnd.recordare.musicxml+xml",
        headers={"Content-Disposition": fname},
    )


@app.post("/api/import")
async def import_score(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        score = import_musicxml(raw, new_id("doc"), title=file.filename or "导入的乐谱")
    except Exception as e:  # parser errors
        raise HTTPException(400, f"MusicXML 解析失败：{e}")
    STORE.create_from(score)
    return _score_payload(score, [])


def _reject(score_id, score: Score, op: str, args: dict, err: edits.EditError) -> dict:
    """Persist the refused edit as a pending proposal, leave model intact."""
    proposal = {
        "id": new_id("prop"),
        "op": op,
        "args": {k: v for k, v in args.items() if k != "baseVersion"},
        "status": "pending",
        "reason": {"code": err.code, "message": err.message,
                   "conflict": err.conflict},
        "baseVersion": score.version,
        "currentVersion": score.version,
    }
    STORE.add_proposal(score_id, proposal)
    proposals = STORE.get_proposals(score_id)
    payload = _score_payload(score, proposals)
    payload["rejected"] = proposal
    return payload


# ---------- static frontend (production) ----------

_STATIC = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=_STATIC / "assets"), name="assets")

    @app.get("/")
    def root_index():
        return FileResponse(_STATIC / "index.html")

    @app.get("/scores/{any_id}")
    def spa_fallback(any_id: str):
        return FileResponse(_STATIC / "index.html")
