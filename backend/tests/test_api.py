"""End-to-end HTTP tests: confirmed-state consistency across GET/layout/export."""
from __future__ import annotations

import os
import tempfile
from fractions import Fraction

os.environ["SCORE_REBAR_DATA"] = tempfile.mkdtemp(prefix="srw-e2e-")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app, STORE  # noqa: E402


def client() -> TestClient:
    # fresh database per test module run
    if os.path.exists(STORE.path):
        os.remove(STORE.path)
    return TestClient(app)


def sounded(payload):
    out = {}
    for v in payload["score"]["voices"]:
        out[v["id"]] = sorted(
            (i["onset"]["num"] / i["onset"]["den"],
             i["duration"]["num"] / i["duration"]["den"],
             i["kind"],
             tuple(sorted(p["step"] for p in i["pitches"])))
            for i in v["items"] if i["kind"] in ("note", "chord")
        )
    return out


def test_first_screen_is_actionable_seed():
    with client() as c:
        r = c.post("/api/scores/seed")
        assert r.status_code == 200
        payload = r.json()
        assert payload["score"]["version"] == 1
        assert len(payload["layout"]["measures"]) == 2
        assert payload["proposals"] == []


def test_meter_change_and_sounded_invariance_and_version():
    with client() as c:
        sid = c.post("/api/scores/seed").json()["score"]["id"]
        before = c.get(f"/api/scores/{sid}").json()
        r = c.post(f"/api/scores/{sid}/meter", json={
            "baseVersion": 1, "fromMeasure": 0, "beats": 3, "beatUnit": 4})
        after = r.json()
        assert after["score"]["version"] == 2
        assert sounded(after) == sounded(before)
        # rebarred: now 3 measures of 3/4
        assert [m["beats"] for m in after["layout"]["measures"]] == [3, 3, 3]
        # sustained G4 at 2..4.5 crosses the new barline at 3
        g_frags = [f for m in after["layout"]["measures"]
                   for f in m["voices"]["v1"]
                   if f["head"] and f["onset"] == {"num": 2, "den": 1}]
        assert g_frags and g_frags[0]["tail"] is False
        # a subsequent GET observes the SAME confirmed state
        got = c.get(f"/api/scores/{sid}").json()
        assert got["score"]["version"] == 2
        assert got["layout"] == after["layout"]


def test_stale_version_rejected_with_409():
    with client() as c:
        sid = c.post("/api/scores/seed").json()["score"]["id"]
        c.post(f"/api/scores/{sid}/meter", json={
            "baseVersion": 1, "fromMeasure": 0, "beats": 3, "beatUnit": 4})
        r = c.post(f"/api/scores/{sid}/meter", json={
            "baseVersion": 1, "fromMeasure": 0, "beats": 2, "beatUnit": 4})
        assert r.status_code == 409


def test_overlap_edit_becomes_pending_proposal_and_keeps_model():
    with client() as c:
        p0 = c.post("/api/scores/seed").json()
        sid = p0["score"]["id"]
        g_id = next(i["id"] for i in p0["score"]["voices"][0]["items"]
                    if i["onset"] == {"num": 2, "den": 1})
        r = c.post(f"/api/scores/{sid}/move", json={
            "baseVersion": 1, "itemId": g_id,
            "onset": {"num": 4, "den": 3}})
        body = r.json()
        assert "rejected" in body
        prop = body["rejected"]
        assert prop["reason"]["code"] == "overlap"
        assert body["score"]["version"] == 1  # model untouched
        # G is still at onset 2
        g = next(i for i in body["score"]["voices"][0]["items"]
                 if i["id"] == g_id)
        assert g["onset"] == {"num": 2, "den": 1}
        # proposal persisted and returned on next GET
        got = c.get(f"/api/scores/{sid}").json()
        assert len(got["proposals"]) == 1
        assert got["proposals"][0]["id"] == prop["id"]
        # dismiss
        d = c.post(f"/api/scores/{sid}/proposals/{prop['id']}/dismiss")
        assert d.json()["proposals"] == []


def test_successful_pitch_and_duration_edit():
    with client() as c:
        p0 = c.post("/api/scores/seed").json()
        sid = p0["score"]["id"]
        g_id = next(i["id"] for i in p0["score"]["voices"][0]["items"]
                    if i["onset"] == {"num": 2, "den": 1})
        r = c.post(f"/api/scores/{sid}/duration", json={
            "baseVersion": 1, "itemId": g_id, "duration": {"num": 1, "den": 1}})
        body = r.json()
        g = next(i for i in body["score"]["voices"][0]["items"]
                 if i["id"] == g_id)
        assert g["duration"] == {"num": 1, "den": 1}
        assert body["score"]["version"] == 2


def test_export_matches_confirmed_layout_then_reimport_same_time():
    with client() as c:
        p0 = c.post("/api/scores/seed").json()
        sid = p0["score"]["id"]
        # rebar to 3/4 first
        c.post(f"/api/scores/{sid}/meter", json={
            "baseVersion": 1, "fromMeasure": 0, "beats": 3, "beatUnit": 4})
        confirmed = c.get(f"/api/scores/{sid}").json()
        xml = c.get(f"/api/scores/{sid}/export").content
        # export reflects 3/4
        assert b"<beats>3</beats>" in xml
        up = c.post("/api/import", files={"file": ("t.musicxml", xml,
                    "application/vnd.recordare.musicxml+xml")})
        assert up.status_code == 200
        reimp = up.json()
        # sounded time identical
        assert sounded(reimp) == sounded(confirmed)
        # triplet preserved
        tg = reimp["score"]["tupletGroups"]
        assert len(tg) == 1 and tg[0]["actual_notes"] == 3
        assert len(tg[0]["member_ids"]) == 3
        # meter preserved
        assert reimp["score"]["meters"][0]["beats"] == 3


def test_lower_voice_unaffected_by_upper_rebar():
    with client() as c:
        sid = c.post("/api/scores/seed").json()["score"]["id"]
        r = c.post(f"/api/scores/{sid}/meter", json={
            "baseVersion": 1, "fromMeasure": 0, "beats": 3, "beatUnit": 4})
        body = r.json()
        lower = [i for i in body["score"]["voices"][1]["items"]
                 if i["kind"] == "note"]
        onsets = sorted(i["onset"]["num"] / i["onset"]["den"] for i in lower)
        assert onsets == [0.0, 4.0]  # still exactly at 0 and 4 QL
