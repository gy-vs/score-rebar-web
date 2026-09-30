"""HTTP API for score-rebar-web.

All mutating endpoints take the client's current ``baseRevision`` and return
the NEW confirmed revision together with the score, projection and export that
belong to it.  A rejected edit (stale revision, or a musically impossible
change) returns HTTP 409/422 and writes nothing; the client keeps its pending
edit and the currently selected event.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import edits
from .model import Duration, ModelError, Pitch, Score, q
from .musicxml_io import import_musicxml
from .store import NotFound, RevisionMismatch, Store

DATA_ROOT = os.environ.get("SCORE_DATA", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "scores"))

app = FastAPI(title="score-rebar-web")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"])
store = Store(DATA_ROOT)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ImportIn(BaseModel):
    musicxml: str
    score_id: str | None = None
    title: str | None = None


class TimeSigIn(BaseModel):
    base_revision: str
    bar_index: int = Field(..., description="bar where the new meter starts")
    beats: int
    beat_unit: int = 4


class EventEditIn(BaseModel):
    base_revision: str
    voice_id: str
    event_id: str
    action: str  # pitch | duration | move | toRest
    pitches: list[str] | None = None
    quarter_length: str | None = None
    tuplet: dict | None = None
    new_start: str | None = None


def _public_doc(doc: dict) -> dict:
    return {"revision": doc["revision"],
            "parent": doc.get("parent"),
            "title": doc["title"],
            "updatedAt": doc.get("updatedAt"),
            "score": doc["score"],
            "projection": doc["projection"],
            "editResult": doc.get("editResult", {})}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/scores")
def list_scores():
    return {"scores": store.list_scores()}


@app.post("/api/scores/import")
def import_score(body: ImportIn):
    import uuid
    score_id = body.score_id or f"score-{uuid.uuid4().hex[:8]}"
    if store.exists(score_id):
        # re-importing the same id replaces it
        pass
    try:
        score = import_musicxml(body.musicxml, score_id=score_id)
        if body.title:
            score.title = body.title
        rev = store.create(score)
    except (ModelError, ValueError, KeyError, AttributeError, SyntaxError) as exc:
        raise HTTPException(status_code=422, detail=f"import failed: {exc}")
    doc = store.get_document(score_id, rev)
    return {"scoreId": score_id, **_public_doc(doc)}


@app.get("/api/scores/{score_id}")
def get_score(score_id: str, rev: str | None = None):
    try:
        doc = store.get_document(score_id, rev)
    except NotFound:
        raise HTTPException(404, "score/revision not found")
    return {"scoreId": score_id, **_public_doc(doc)}


@app.get("/api/scores/{score_id}/musicxml")
def get_musicxml(score_id: str, rev: str | None = None):
    try:
        xml, rev_used = store.get_musicxml(score_id, rev)
    except NotFound:
        raise HTTPException(404, "score/revision not found")
    return PlainTextResponse(xml, media_type="application/vnd.recordare.musicxml+xml",
                             headers={"X-Revision": rev_used})


@app.post("/api/scores/{score_id}/timesig")
def set_timesig(score_id: str, body: TimeSigIn):
    def mutate(score: Score):
        from .notation import build_bars
        bars = build_bars(score)
        if body.bar_index < 0 or body.bar_index >= len(bars):
            raise ModelError(f"bar {body.bar_index} does not exist")
        start = bars[body.bar_index].start
        edits.set_time_signature(score, start, body.beats, body.beat_unit)
        return {"start": str(start), "beats": body.beats,
                "beatUnit": body.beat_unit}
    return _commit(score_id, body.base_revision, mutate,
                   f"time {body.beats}/{body.beat_unit} at bar {body.bar_index}")


@app.post("/api/scores/{score_id}/events")
def edit_event(score_id: str, body: EventEditIn):
    def mutate(score: Score):
        if body.action == "pitch":
            if not body.pitches:
                raise ModelError("pitch edit requires pitches")
            edits.change_pitch(score, body.voice_id, body.event_id, body.pitches)
        elif body.action == "toRest":
            edits.to_rest(score, body.voice_id, body.event_id)
        elif body.action == "duration":
            if body.quarter_length is None:
                raise ModelError("duration edit requires quarterLength")
            tup = TupletSpec_from(body.tuplet) if body.tuplet else None
            edits.change_duration(score, body.voice_id, body.event_id,
                                  Duration(q(body.quarter_length), tup))
        elif body.action == "move":
            if body.new_start is None:
                raise ModelError("move edit requires newStart")
            new_dur = None
            if body.quarter_length is not None:
                tup = TupletSpec_from(body.tuplet) if body.tuplet else None
                new_dur = Duration(q(body.quarter_length), tup)
            edits.move_event(score, body.voice_id, body.event_id,
                             q(body.new_start), new_dur)
        else:
            raise ModelError(f"unknown action {body.action!r}")
        return {"action": body.action, "eventId": body.event_id}
    return _commit(score_id, body.base_revision, mutate,
                   f"{body.action} {body.event_id}")


def TupletSpec_from(d: dict):
    from .model import TupletSpec
    return TupletSpec(int(d["actual"]), int(d["normal"]),
                      d.get("unit", "eighth"))


def _commit(score_id, base_rev, mutate, label):
    try:
        rev, doc = store.commit(score_id, base_rev, mutate, label)
    except NotFound:
        raise HTTPException(404, "score not found")
    except RevisionMismatch as exc:
        # 409: client must refetch; its pending edit is preserved client-side.
        head = store.head_revision(score_id)
        raise HTTPException(409, detail={"error": "revision_stale",
                                         "message": str(exc),
                                         "headRevision": head})
    except ModelError as exc:
        # 422: edit musically impossible; nothing was written.
        raise HTTPException(422, detail={"error": "edit_rejected",
                                         "message": str(exc)})
    return {"scoreId": score_id, **_public_doc(doc)}


# ---------------------------------------------------------------------------
# Static frontend (production build)
# ---------------------------------------------------------------------------

_STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "frontend", "dist")
if os.path.isdir(_STATIC):
    from fastapi.staticfiles import StaticFiles

    @app.get("/")
    def index():
        from fastapi.responses import FileResponse
        return FileResponse(os.path.join(_STATIC, "index.html"))

    app.mount("/assets",
              StaticFiles(directory=os.path.join(_STATIC, "assets")),
              name="assets")
