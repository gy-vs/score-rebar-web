"""MusicXML roundtrip built on music21.

Import walks parts/measures/voices and produces our canonical model
(absolute-time voice timelines), resolving chords and tied note chains into
*one* sounded event each.  Export walks the *derived layout* (measure
slices), so what is written is exactly the confirmed structure the editor
shows — ties across barlines are generated from the layout, never stored.
"""
from __future__ import annotations

import tempfile
from fractions import Fraction
from typing import Optional

from music21 import chord as chord_mod
from music21 import converter
from music21 import duration as dur_mod
from music21 import meter as meter_mod
from music21 import note as note_mod
from music21 import stream
from music21 import tie as tie_mod

from .layout import derive_layout, measure_grid
from .models import (
    MeterRegion, Pitch, Score, TupletGroup, Voice, VoiceItem, new_id,
)
from .rational import as_q

_ALTER = {"double-sharp": 2, "sharp": 1, "natural": 0, "flat": -1,
          "double-flat": -2, "natural-sharp": 1, "natural-flat": -1}

_CODE_TYPE = {"w": "whole", "h": "half", "q": "quarter", "e": "eighth",
              "16": "16th", "32": "32nd"}

_TOKEN_QL = {"w": Fraction(4), "h": Fraction(2), "q": Fraction(1),
             "e": Fraction(1, 2), "16": Fraction(1, 4), "32": Fraction(1, 8)}


def token_ql(code: str, dots: int) -> Fraction:
    base = _TOKEN_QL[code]
    if dots == 0:
        return base
    return base + base / 2 if dots == 1 else base + base / 2 + base / 4


# ---------------- import ----------------

def _pitch_of(p) -> Pitch:
    alter = 0
    acc = getattr(p, "accidental", None)
    if acc is not None and acc.name in _ALTER:
        alter = _ALTER[acc.name]
    return Pitch(step=p.step, alter=alter, octave=int(p.implicitOctave))


def _tuplet_info(el) -> Optional[tuple[int, int]]:
    if getattr(el, "duration", None) is None:
        return None
    for t in el.duration.tuplets:
        return int(t.numberNotesActual), int(t.numberNotesNormal)
    return None


def _normalize_rests(voice: Voice, end: Fraction | None = None) -> None:
    """Merge adjacent/implicit rests into maximal rest runs (canonical form).

    ``end`` is the part's total measure span: a trailing rest is extended to
    fill the final measure, matching what the engraver wrote and what our own
    layout generates.
    """
    sounded = [it for it in voice.items
               if it.kind != "rest" or it.tuplet_group_id is not None]
    sounded.sort(key=lambda it: it.onset)
    runs: list[tuple[Fraction, Fraction]] = []
    cursor = Fraction(0)
    for it in sounded:
        if it.onset > cursor:
            runs.append((cursor, it.onset))
        cursor = max(cursor, it.end)
    if end is None:
        end = max((it.end for it in voice.items), default=Fraction(0))
    if cursor < end:
        runs.append((cursor, end))
    kept = list(sounded)
    for s, e in runs:
        kept.append(VoiceItem(id=new_id("ev"), kind="rest", onset=s,
                              duration=e - s))
    voice.items = sorted(kept, key=lambda it: it.onset)


def import_musicxml(data: bytes, score_id: str, title: str = "导入的乐谱") -> Score:
    parsed = converter.parseData(data)
    # parsed.parts flattens nested voices into pseudo-parts; take the real
    # top-level Part objects only.
    parts = list(parsed.getElementsByClass(stream.Part))
    if not parts:
        raise ValueError("MusicXML 中没有声部（part）")

    voices: list[Voice] = []
    tuplet_groups: list[TupletGroup] = []

    for v_idx, part in enumerate(parts):
        voice_model = Voice(id=f"v{v_idx + 1}",
                            name=part.partName or f"声部 {v_idx + 1}")
        # A tie may only join the *immediately following* note of the same
        # pitches on the same staff-voice (there is no rest or other pitch
        # between the two slices). Tracking the previous element per
        # staff-voice avoids merging a later repeated note with an earlier
        # tie of the same pitch.
        last_sounded: dict[str, VoiceItem] = {}
        last_end: dict[str, Fraction] = {}
        # tuplet groups continue while consecutive members on a staff-voice
        # carry the same actual/normal numbers.
        open_tuplet: dict[str, dict] = {}

        measures = list(part.getElementsByClass(stream.Measure))
        part_end = max(
            (as_q(float(m.getOffsetInHierarchy(part)))
             + as_q(float(m.barDuration.quarterLength)) for m in measures),
            default=Fraction(0))

        for m in measures:
            sub_voices = list(m.getElementsByClass(stream.Voice))
            voice_containers = sub_voices if sub_voices else [m]
            for sv_index, sv in enumerate(voice_containers):
                # staff-voice slot must be stable across measures even though
                # music21 creates fresh Voice objects per measure; key by the
                # slot position, preferring the MusicXML voice id when it is
                # the conventional '1'/'2' style.
                raw_id = getattr(sv, "id", None)
                svid = str(raw_id) if (sub_voices and str(raw_id).isdigit()) \
                    else f"slot{sv_index}"
                v_start = as_q(float(sv.getOffsetInHierarchy(part)))
                for el in sv.notesAndRests:
                    # absolute QL within the part, surviving measure/voice
                    # resets (the <backup>-style return to measure start)
                    onset = v_start + as_q(float(el.getOffsetInHierarchy(sv)))
                    ql = as_q(float(el.quarterLength))
                    tinfo = _tuplet_info(el)

                    if el.isRest:
                        item = VoiceItem(id=new_id("ev"), kind="rest",
                                         onset=onset, duration=ql)
                        voice_model.items.append(item)
                        last_sounded.pop(svid, None)
                        last_end[svid] = onset + ql
                        if tinfo:
                            grp = _continue_tuplet(
                                open_tuplet, tuplet_groups, svid, tinfo, item)
                            item.tuplet_group_id = grp.id
                        else:
                            open_tuplet.pop(svid, None)
                        continue

                    pitches = [_pitch_of(p) for p in (
                        el.pitches if isinstance(el, chord_mod.Chord)
                        else [el.pitch])]
                    tie_type = el.tie.type if el.tie is not None else None
                    kind = "chord" if len(pitches) > 1 else "note"

                    prev = last_sounded.get(svid)
                    contiguous = (prev is not None
                                  and last_end.get(svid) == onset
                                  and prev.pitches == pitches)
                    if tie_type in ("stop", "continue") and contiguous:
                        item = prev
                        item.duration += ql
                    else:
                        item = VoiceItem(id=new_id("ev"), kind=kind,
                                         onset=onset, duration=ql,
                                         pitches=pitches)
                        voice_model.items.append(item)

                    if tinfo:
                        grp = _continue_tuplet(
                            open_tuplet, tuplet_groups, svid, tinfo, item)
                        item.tuplet_group_id = grp.id
                    else:
                        open_tuplet.pop(svid, None)

                    if tie_type in ("start", "continue"):
                        last_sounded[svid] = item
                    else:
                        last_sounded.pop(svid, None)
                    last_end[svid] = onset + ql

        _normalize_rests(voice_model, part_end)
        voices.append(voice_model)

    meters: list[MeterRegion] = []
    for ts in parts[0].recurse().getElementsByClass(meter_mod.TimeSignature):
        m = ts.getContextByClass(stream.Measure)
        onset = as_q(float(m.offset)) if m is not None else Fraction(0)
        meters.append(MeterRegion(onset=onset, beats=int(ts.numerator),
                                  beat_unit=int(ts.denominator)))
    if not meters:
        meters = [MeterRegion(onset=Fraction(0), beats=4, beat_unit=4)]

    return Score(id=score_id, title=title, version=1,
                 voices=voices, meters=meters, tuplet_groups=tuplet_groups)


def _continue_tuplet(open_tuplet, groups, svid: str,
                     tinfo: tuple[int, int], item: VoiceItem) -> TupletGroup:
    """Reuse the open group on this staff-voice while members stay adjacent."""
    run = open_tuplet.get(svid)
    if run is None or (run["actual"], run["normal"]) != tinfo:
        grp = TupletGroup(id=new_id("tg"), actual_notes=tinfo[0],
                          normal_notes=tinfo[1], member_ids=[item.id])
        groups.append(grp)
        open_tuplet[svid] = {"actual": tinfo[0], "normal": tinfo[1],
                             "group": grp}
        return grp
    grp = run["group"]
    if item.id not in grp.member_ids:
        grp.member_ids.append(item.id)
    return grp


# ---------------- export ----------------

def _set_duration(el, code: str, dots: int, tuplet_group: Optional[TupletGroup]) -> None:
    el.duration.type = _CODE_TYPE[code]
    el.duration.dots = dots
    if tuplet_group is not None:
        t = dur_mod.Tuplet(tuplet_group.actual_notes, tuplet_group.normal_notes)
        t.durationActual = dur_mod.durationTupleFromTypeDots("eighth", 0)
        t.durationNormal = dur_mod.durationTupleFromTypeDots("quarter", 0)
        el.duration.appendTuplet(t)


def _make_element(token: dict, frag: dict, group_by_id: dict):
    tuplet_group = group_by_id.get(token.get("tupletGroupId"))
    if frag["kind"] == "rest":
        if token.get("wholeMeasure"):
            el = note_mod.Rest()
            el.fullMeasure = True
            return el
        el = note_mod.Rest()
        _set_duration(el, token["duration"], token["dots"], tuplet_group)
        return el

    pitches = [Pitch.from_dict(p) for p in frag["pitches"]]
    if frag["kind"] == "chord" and len(pitches) > 1:
        el = chord_mod.Chord([p.name() for p in pitches])
    else:
        el = note_mod.Note(pitches[0].name())
    _set_duration(el, token["duration"], token["dots"], tuplet_group)

    n_tok = len(frag["tokens"])
    is_first = token["seq"] == 0
    is_last = token["seq"] == n_tok - 1
    frag_has_prev = not frag["head"]
    frag_has_next = not frag["tail"]
    tie_in = token["tieIn"] or (frag_has_prev and is_first)
    tie_out = token["tieOut"] or (frag_has_next and is_last)
    if tie_in and tie_out:
        el.tie = tie_mod.Tie("continue")
    elif tie_in:
        el.tie = tie_mod.Tie("stop")
    elif tie_out:
        el.tie = tie_mod.Tie("start")
    return el


def export_musicxml(score: Score) -> bytes:
    """Write one MusicXML ``<part>`` per model voice.

    A model voice is an independent timeline (its own events never move when
    another voice is rebarred), so mapping 1:1 to parts preserves that
    independence on roundtrip.  Measure-local fragments are written with the
    ties/brackets derived by the layout.
    """
    layout = derive_layout(score)
    grid = measure_grid(score)
    group_by_id = {g.id: g for g in score.tuplet_groups}

    sc = stream.Score()
    for voice_def in score.voices:
        part = stream.Part()
        part.partName = voice_def.name
        part.id = voice_def.id

        for mi, measure in enumerate(layout["measures"]):
            mm = stream.Measure(number=mi + 1)
            region_at = next((r for r in score.meters if r.onset == grid[mi][0]),
                             None)
            if region_at is not None:
                mm.insert(0, meter_mod.TimeSignature(
                    f"{region_at.beats}/{region_at.beat_unit}"))

            for frag in measure["voices"][voice_def.id]:
                cursor = as_q(frag["localOnset"])
                for tok in frag["tokens"]:
                    el = _make_element(tok, frag, group_by_id)
                    mm.insert(float(cursor), el)
                    cursor += token_ql(tok["duration"], tok["dots"])
            part.append(mm)
        sc.append(part)

    with tempfile.NamedTemporaryFile(suffix=".musicxml", delete=False) as tf:
        path = tf.name
    sc.write("musicxml", fp=path)
    with open(path, "rb") as f:
        return f.read()
