"""Tests for confirmed-state revision semantics in the store/API."""
import os
import tempfile

import pytest

from fastapi.testclient import TestClient

from backend import app as app_module
from backend.store import Store

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "seed_musicxml", "seed.xml")


@pytest.fixture
def client():
    tmp = tempfile.mkdtemp()
    app_module.store = Store(os.path.join(tmp, "scores"))
    app_module.DATA_ROOT = tmp
    with TestClient(app_module.app) as c:
        xml = open(SEED).read()
        r = c.post("/api/scores/import", json={"musicxml": xml,
                                               "score_id": "seed"})
        assert r.status_code == 200, r.text
        yield c


def test_first_screen_has_actionable_score(client):
    r = client.get("/api/scores/seed")
    assert r.status_code == 200
    doc = r.json()
    assert len(doc["projection"]["bars"]) == 2
    assert doc["projection"]["voices"]


def test_rebar_returns_one_consistent_revision(client):
    doc = client.get("/api/scores/seed").json()
    rev = doc["revision"]
    r = client.post("/api/scores/seed/timesig",
                    json={"base_revision": rev, "bar_index": 0,
                          "beats": 3, "beat_unit": 4})
    assert r.status_code == 200
    new_doc = r.json()
    new_rev = new_doc["revision"]
    assert new_rev != rev
    assert [b["timeLabel"] for b in new_doc["projection"]["bars"]] == \
        ["3/4", "3/4", "3/4"]
    # export at that revision contains the new meter
    xml = client.get(f"/api/scores/seed/musicxml?rev={new_rev}").text
    assert "<beats>3</beats>" in xml


def test_stale_revision_is_rejected_and_not_written(client):
    doc = client.get("/api/scores/seed").json()
    old = doc["revision"]
    r1 = client.post("/api/scores/seed/timesig",
                     json={"base_revision": old, "bar_index": 0,
                           "beats": 3, "beat_unit": 4})
    assert r1.status_code == 200
    # retry with the old revision
    r2 = client.post("/api/scores/seed/timesig",
                     json={"base_revision": old, "bar_index": 0,
                           "beats": 2, "beat_unit": 4})
    assert r2.status_code == 409
    head = client.get("/api/scores/seed").json()["revision"]
    assert head == r1.json()["revision"]  # still the 3/4 commit


def test_impossible_edit_rejected_without_dropping_event(client):
    doc = client.get("/api/scores/seed").json()
    rev = doc["revision"]
    voice = doc["score"]["voices"][0]["voiceId"]
    g = next(e for e in doc["score"]["voices"][0]["events"]
             if any(p["name"] == "G4" for p in e["pitches"]))
    r = client.post("/api/scores/seed/events",
                    json={"base_revision": rev, "voice_id": voice,
                          "event_id": g["eventId"], "action": "move",
                          "new_start": "0"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "edit_rejected"
    # the event is still present and unchanged
    after = client.get("/api/scores/seed").json()
    g2 = next(e for e in after["score"]["voices"][0]["events"]
              if e["eventId"] == g["eventId"])
    assert g2["start"] == g["start"] and g2["end"] == g["end"]
    assert after["revision"] == rev  # no revision created


def test_pitch_edit_then_export_and_reimport(client):
    doc = client.get("/api/scores/seed").json()
    rev = doc["revision"]
    voice = doc["score"]["voices"][0]["voiceId"]
    g = next(e for e in doc["score"]["voices"][0]["events"]
             if any(p["name"] == "G4" for p in e["pitches"]))
    r = client.post("/api/scores/seed/events",
                    json={"base_revision": rev, "voice_id": voice,
                          "event_id": g["eventId"], "action": "pitch",
                          "pitches": ["B4"]})
    assert r.status_code == 200
    new_rev = r.json()["revision"]
    xml = client.get(f"/api/scores/seed/musicxml?rev={new_rev}").text
    # re-import the exported file as a fresh score
    r2 = client.post("/api/scores/import",
                     json={"musicxml": xml, "score_id": "copy"})
    assert r2.status_code == 200
    voices = r2.json()["score"]["voices"]
    names = [p["name"] for e in voices[0]["events"] if e["kind"] != "rest"
             for p in e["pitches"]]
    assert "B4" in names and "G4" not in names
