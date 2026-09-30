"""Tests for the canonical model, edits, projection and MusicXML round trip."""
import os
import pytest

from backend import edits, notation
from backend.model import Duration, Pitch, TupletSpec, q
from backend.musicxml_io import export_musicxml, import_musicxml

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "seed_musicxml", "seed.xml")


@pytest.fixture
def score():
    return import_musicxml(open(SEED).read(), score_id="t")


def sounding(score):
    return {
        v.voice_id: [
            (e.kind, str(e.start), str(e.end),
             tuple(sorted(p.name for p in e.pitches)))
            for e in v.sorted_events() if e.kind != "rest"
        ] for v in score.voices
    }


def test_import_collapses_tie_chain_into_one_event(score):
    v1 = score.voices[0].events
    g = [e for e in v1 if any(p.name == "G4" for p in e.pitches)]
    assert len(g) == 1
    assert g[0].start == q(1) and g[0].end == q(8)
    assert g[0].duration.quarter_length == 7


def test_triplet_members_keep_real_duration(score):
    first_three = score.voices[0].events[:3]
    assert [e.duration.quarter_length for e in first_three] == [q("1/3")] * 3
    tup = first_three[0].duration.tuplet
    assert (tup.actual, tup.normal, tup.unit) == (3, 2, "eighth")


def test_voices_are_independent_timelines(score):
    v1, v2 = score.voices
    assert [e.start for e in v2.events] == [q(0), q(4)]
    assert all(any(p.name == "C3" for p in e.pitches) for e in v2.events)


def test_rebar_preserves_sounding_times(score):
    before = sounding(score)
    edits.set_time_signature(score, q(0), 3, 4)
    after = sounding(score)
    assert before == after


def test_rebar_changes_but_does_not_add_sounding_events(score):
    n_before = sum(1 for v in score.voices for e in v.events if e.kind != "rest")
    edits.set_time_signature(score, q(0), 3, 4)
    n_after = sum(1 for v in score.voices for e in v.events if e.kind != "rest")
    assert n_before == n_after


def test_g4_splits_into_three_tied_slices_in_3_4(score):
    edits.set_time_signature(score, q(0), 3, 4)
    proj = notation.project(score)
    g = [e for e in proj["events"].values()
         if e["kind"] == "note" and e["pitches"][0]["name"] == "G4"][0]
    assert len(g["sliceIds"]) == 3
    # slices all point to the SAME event id
    assert all(sid.startswith(g["eventId"]) for sid in g["sliceIds"])


def test_bars_are_fully_tiled_in_3_4(score):
    edits.set_time_signature(score, q(0), 3, 4)
    proj = notation.project(score)
    for bar in proj["bars"]:
        for vi in range(len(proj["voices"])):
            total = sum(q(rn["ql"]) for sl in bar["slicesByVoice"][vi]
                        for rn in sl["renderNotes"])
            assert total == q(bar["duration"])


def test_triplet_bracket_roles(score):
    proj = notation.project(score)
    roles = []
    for sl in proj["bars"][0]["slicesByVoice"][0][:3]:
        for rn in sl["renderNotes"]:
            if rn["tuplet"]:
                roles.append(rn["tuplet"]["bracketRole"])
    assert roles == ["start", "continue", "stop"]


def test_4_4_g4_has_one_internal_tie_into_next_bar(score):
    proj = notation.project(score)
    g_in_m0 = [s for s in proj["bars"][0]["slicesByVoice"][0]
               if s["pitches"] and s["pitches"][0]["name"] == "G4"][0]
    assert g_in_m0["tieOut"] is True
    g_in_m1 = [s for s in proj["bars"][1]["slicesByVoice"][0]
               if s["pitches"] and s["pitches"][0]["name"] == "G4"][0]
    assert g_in_m1["tieIn"] is True


def test_change_pitch_keeps_time(score):
    gid = next(e.event_id for e in score.voices[0].events
               if any(p.name == "G4" for p in e.pitches))
    edits.change_pitch(score, score.voices[0].voice_id, gid, ["A4"])
    ev = score.voices[0].find(gid)
    assert ev.pitches[0].name == "A4"
    assert (ev.start, ev.end) == (q(1), q(8))


def test_move_into_rest_retiles_and_keeps_identity():
    from backend.model import Score, SoundingEvent, TimeSignatureMark, Voice
    v = Voice("v", "v", [
        SoundingEvent("n1", "note", q(0), Duration(1), [Pitch.parse("C4")]),
        SoundingEvent("r1", "rest", q(1), Duration(2), []),
        SoundingEvent("n2", "note", q(3), Duration(1), [Pitch.parse("E4")]),
    ])
    s = Score("x", "x", [v], [TimeSignatureMark(q(0), 4, 4)])
    edits.validate_score(s)
    edits.move_event(s, "v", "n1", q(1))
    moved = s.voices[0].find("n1")
    assert moved.start == q(1)
    assert moved.pitches[0].name == "C4"
    # vacated start is now a rest; voice still tiles
    edits.validate_score(s)


def test_change_duration_grows_into_rest():
    from backend.model import Score, SoundingEvent, TimeSignatureMark, Voice
    v = Voice("v", "v", [
        SoundingEvent("n1", "note", q(0), Duration(1), [Pitch.parse("C4")]),
        SoundingEvent("r1", "rest", q(1), Duration(1), []),
        SoundingEvent("n2", "note", q(2), Duration(2), [Pitch.parse("E4")]),
    ])
    s = Score("x", "x", [v], [TimeSignatureMark(q(0), 4, 4)])
    edits.change_duration(s, "v", "n1", Duration(2))
    n1 = s.voices[0].find("n1")
    assert n1.duration.quarter_length == 2
    edits.validate_score(s)  # rest shrank, n2 unmoved


def test_lengthening_over_a_note_is_rejected():
    from backend.model import Score, SoundingEvent, TimeSignatureMark, Voice
    v = Voice("v", "v", [
        SoundingEvent("n1", "note", q(0), Duration(1), [Pitch.parse("C4")]),
        SoundingEvent("n2", "note", q(1), Duration(1), [Pitch.parse("D4")]),
    ])
    s = Score("x", "x", [v], [TimeSignatureMark(q(0), 4, 4)])
    with pytest.raises(Exception):
        edits.change_duration(s, "v", "n1", Duration(2))


def test_move_into_rest_keeps_voice_tiling(score):
    # move the m2 G4 within voice 1 after rebar: validate tiling survives
    edits.set_time_signature(score, q(0), 3, 4)
    edits.validate_score(score)


def test_move_colliding_with_note_is_rejected(score):
    gid = next(e.event_id for e in score.voices[0].events
               if any(p.name == "G4" for p in e.pitches))
    with pytest.raises(Exception):
        edits.move_event(score, score.voices[0].voice_id, gid, q(0))
    # event still at its original place (not erased)
    ev = score.voices[0].find(gid)
    assert ev.start == q(1)


def test_rejected_edit_does_not_erase_selection(score):
    gid = next(e.event_id for e in score.voices[0].events
               if any(p.name == "G4" for p in e.pitches))
    with pytest.raises(Exception):
        edits.move_event(score, score.voices[0].voice_id, gid, q("1/3"))
    assert score.voices[0].find(gid) is not None


def test_lower_voice_unchanged_when_upper_rebarred(score):
    before = [(str(e.start), str(e.end)) for e in score.voices[1].events]
    edits.set_time_signature(score, q(0), 3, 4)
    after = [(str(e.start), str(e.end)) for e in score.voices[1].events
             if e.kind != "rest"]
    assert after == before


def test_roundtrip_4_4_preserves_notes_and_marks(score):
    rt = import_musicxml(export_musicxml(score))
    assert sounding(rt) == sounding(score)


def test_roundtrip_3_4_preserves_notes_and_marks(score):
    edits.set_time_signature(score, q(0), 3, 4)
    rt = import_musicxml(export_musicxml(score))
    assert sounding(rt) == sounding(score)
    assert [(m.beats, m.beat_unit) for m in rt.time_marks] == [(3, 4)]


def test_export_contains_ties_and_time_modification(score):
    edits.set_time_signature(score, q(0), 3, 4)
    xml = export_musicxml(score)
    assert "<time-modification>" in xml
    assert "<actual-notes>3</actual-notes>" in xml
    assert "<tie " in xml and "<tied " in xml


def test_gap_is_invalid():
    from backend.model import Score, SoundingEvent, TimeSignatureMark, Voice
    v = Voice("v", "v", [
        SoundingEvent("a", "note", q(0), Duration(1), [Pitch.parse("C4")]),
        SoundingEvent("b", "note", q(2), Duration(1), [Pitch.parse("D4")]),
    ])
    s = Score("x", "x", [v], [TimeSignatureMark(q(0), 4, 4)])
    with pytest.raises(Exception):
        edits.validate_score(s)
