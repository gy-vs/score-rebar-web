"""Backend invariant tests:

* rebarring never changes sounded-event time
* ties/rest-splits are derived and re-derived identically
* MusicXML export -> import preserves sounded events in both meters
* edits mutate real voice time; overlaps are rejected into proposals
"""
from __future__ import annotations

from fractions import Fraction

import pytest

from app import edits
from app.layout import derive_layout, measure_grid
from app.models import MeterRegion, Pitch, seed_score
from app.musicxml import export_musicxml, import_musicxml


def sounded_timeline(score):
    out = {}
    for v in score.voices:
        out[v.id] = sorted(
            (it.onset, it.duration, it.kind,
             tuple(sorted(p.name() for p in it.pitches)),
             it.tuplet_group_id is not None)
            for it in v.items if it.kind in ("note", "chord")
        )
    return out


def test_measure_grid_44_and_34():
    s = seed_score()
    assert [(a, b, (r.beats, r.beat_unit)) for a, b, r in measure_grid(s)] == [
        (Fraction(0), Fraction(4), (4, 4)),
        (Fraction(4), Fraction(4), (4, 4)),
    ]
    edits.set_meter(s, 0, 3, 4)
    assert [(float(a), float(b)) for a, b, _ in measure_grid(s)] == [
        (0, 3), (3, 3), (6, 3)
    ]


def test_sounded_time_invariant_under_meter_change():
    s44 = seed_score()
    before = sounded_timeline(s44)
    s34 = seed_score()
    edits.set_meter(s34, 0, 3, 4)
    after = sounded_timeline(s34)
    assert before == after
    assert s34.version == s44.version + 1


def test_ties_are_derived_not_stored_and_change_with_meter():
    s = seed_score()
    l44 = derive_layout(s)
    # 4/4: G event at 2 -> fragment 2..4 tie-out, fragment 4..4.5 tie-in
    g_id = [i.id for i in s.voices[0].items
            if i.kind == "note" and i.onset == 2][0]
    frags44 = [(Fraction(f["onset"]["num"], f["onset"]["den"]),
                Fraction(f["duration"]["num"], f["duration"]["den"]),
                f["head"], f["tail"])
               for m in l44["measures"] for f in m["voices"]["v1"]
               if f["kind"] == "note" and f["itemId"] == g_id]
    assert frags44[0] == (Fraction(2), Fraction(2), True, False)
    assert frags44[1] == (Fraction(4), Fraction(1, 2), False, True)

    edits.set_meter(s, 0, 3, 4)
    l34 = derive_layout(s)
    g_frags = [f for m in l34["measures"] for f in m["voices"]["v1"]
               if f["kind"] == "note" and f["itemId"] == g_id]
    heads = [(Fraction(f["onset"]["num"], f["onset"]["den"]),
              Fraction(f["duration"]["num"], f["duration"]["den"]),
              f["head"], f["tail"]) for f in g_frags]
    assert (Fraction(2), Fraction(1), True, False) in heads
    assert (Fraction(3), Fraction(3, 2), False, True) in heads


def test_lower_voice_independent_when_upper_rebarred():
    s = seed_score()
    edits.set_meter(s, 0, 3, 4)
    lay = derive_layout(s)
    lower = [(Fraction(f["onset"]["num"], f["onset"]["den"]),
              Fraction(f["duration"]["num"], f["duration"]["den"]))
             for m in lay["measures"] for f in m["voices"]["v2"]
             if f["kind"] == "note"]
    # two C3 events at 0 and 4 are still at exactly those sounded times
    assert lower[0][0] == 0
    assert any(o == 4 for o, _ in lower)
    # fragments align to new grid: 0..3, 3..4, 4..6, 6..8
    assert lower[0] == (0, 3)


def test_triplet_exact_duration_preserved():
    s = seed_score()
    for it in s.voices[0].items[:3]:
        assert it.duration == Fraction(2, 3)
    edits.set_meter(s, 0, 3, 4)
    for it in s.voices[0].items[:3]:
        assert it.duration == Fraction(2, 3)
    # tokens still flagged as tuplet members in both meters
    lay = derive_layout(s)
    marked = [t for m in lay["measures"] for f in m["voices"]["v1"]
              for t in f["tokens"] if t["tupletGroupId"]]
    assert len(marked) == 3


def test_whole_measure_rest_rendered_single_glyph():
    s = seed_score()
    edits.set_meter(s, 0, 3, 4)
    lay = derive_layout(s)
    m2 = lay["measures"][2]["voices"]["v1"]
    whole = [f for f in m2 if f["kind"] == "rest"
             and any(t["wholeMeasure"] for t in f["tokens"])]
    assert len(whole) == 1


def test_move_conflicts_are_rejected():
    s = seed_score()
    # moving the sustained G4 onto the triplet B4's time collides
    g = [i for i in s.voices[0].items if i.onset == 2][0]
    with pytest.raises(edits.EditError) as ei:
        edits.move_item(s, g.id, Fraction(4, 3))
    assert ei.value.code == "overlap"
    # model untouched: G still at 2, version unchanged
    assert s.voices[0].get(g.id).onset == 2
    assert s.version == 1


def test_move_updates_real_timeline():
    s = seed_score()
    g = [i for i in s.voices[0].items if i.onset == 2][0]
    edits.move_item(s, g.id, Fraction(5))
    assert s.voices[0].get(g.id).onset == 5
    lay = derive_layout(s)
    found = [f for m in lay["measures"] for f in m["voices"]["v1"]
             if f["itemId"] == g.id]
    assert Fraction(found[0]["onset"]["num"], found[0]["onset"]["den"]) == 5


def test_tuplet_members_locked_from_individual_edit():
    s = seed_score()
    first = s.voices[0].items[0]
    with pytest.raises(edits.EditError) as ei:
        edits.change_duration(s, first.id, Fraction(1, 4))
    assert ei.value.code == "tuplet_locked"
    with pytest.raises(edits.EditError):
        edits.move_item(s, first.id, Fraction(1))


def test_change_pitch_rest_becomes_sounded():
    s = seed_score()
    rest = [i for i in s.voices[0].items if i.kind == "rest"][0]
    edits.change_pitch(s, rest.id, [{"step": "D", "alter": 0, "octave": 5}])
    assert rest.kind == "note"
    assert rest.pitches[0].name() == "D5"


@pytest.mark.parametrize("beats", [4, 3])
def test_musicxml_roundtrip_sounded_events(beats):
    s = seed_score()
    edits.set_meter(s, 0, beats, 4)
    xml = export_musicxml(s)
    s2 = import_musicxml(xml, "rt", "rt")
    assert sounded_timeline(s2) == sounded_timeline(s)
    assert [(m.beats, m.beat_unit) for m in s2.meters] == [(beats, 4)]
    # triplet survives
    tg = s2.tuplet_groups
    assert len(tg) == 1 and tg[0].actual_notes == 3 and len(tg[0].member_ids) == 3
    # export the import again: stable (idempotent)
    s3 = import_musicxml(export_musicxml(s2), "rt2", "rt2")
    assert sounded_timeline(s3) == sounded_timeline(s)


def test_unknown_duration_rejected():
    s = seed_score()
    g = [i for i in s.voices[0].items if i.onset == 2][0]
    with pytest.raises(edits.EditError):
        edits.change_duration(s, g.id, Fraction(1, 7))
