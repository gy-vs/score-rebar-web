"""MusicXML import / export via music21.

music21 is used as the *read/write library* (it understands ``<backup>``,
divisions, time-modification, ties).  The semantic model is our own: on import
we collapse tied note chains into ONE :class:`SoundingEvent` per voice at an
absolute beat, and on export we emit the display slices produced by the bar
projection as tied notes.  Round-tripping therefore preserves *sounding time*,
while the visual barring/tie layout is regenerated from the model.
"""
from __future__ import annotations

from fractions import Fraction

from music21 import converter as m21_converter
from music21 import duration as m21_duration
from music21 import meter as m21_meter
from music21 import note as m21_note
from music21 import stream as m21_stream
from music21 import tie as m21_tie_mod
from music21.musicxml import m21ToXml

from .model import (
    Duration, Pitch, QL, Score, SoundingEvent, TimeSignatureMark, Voice, q,
)
from . import notation


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_musicxml(xml_text: str, score_id: str = "seed") -> Score:
    sc = m21_converter.parseData(xml_text)
    parts = list(sc.parts)
    if not parts:
        raise ValueError("MusicXML contains no parts")
    # Scope: one staff with multiple voices is our canonical shape (the user's
    # two-voice example). Multiple <part> elements are treated as voices of one
    # staff, which preserves their independent time lines equally well.
    voices: list[Voice] = []
    time_marks: list[TimeSignatureMark] = []
    part_names = []

    # Flatten all parts to (part_index, voice_label) groups; within one part
    # music21's <voice> tags are kept distinct (the user's backup layout),
    # while multiple <part> elements are also treated as independent voices.
    raw_voices: dict[str, list[dict]] = {}
    voice_order: list[str] = []
    voice_names: dict[str, str] = {}
    counter = [0]

    def nid() -> str:
        counter[0] += 1
        return f"ev-{counter[0]:04d}"

    for pi, part in enumerate(parts):
        pname = part.partName if part.partName else f"Voice"
        measures = list(part.getElementsByClass("Measure"))
        for m in measures:
            moff = Fraction(float(m.offset)).limit_denominator(100000)
            # time signatures declared on this measure
            for ts in m.getElementsByClass(m21_meter.TimeSignature):
                tstart = moff + Fraction(float(ts.offset)).limit_denominator(100000)
                mark = TimeSignatureMark(tstart, ts.numerator, ts.denominator)
                if all(mark.start != x.start for x in time_marks):
                    time_marks.append(mark)

            containers = list(m.voices) if m.voices else [m]
            for ci, container in enumerate(containers):
                vid = str(container.id) if container is not m else "1"
                voice_key = f"p{pi}:v{vid}"
                if voice_key not in raw_voices:
                    raw_voices[voice_key] = []
                    voice_order.append(voice_key)
                    suffix = "" if len(parts) <= 1 and len(containers) <= 1 \
                        else f" {ci + 1}" if len(parts) <= 1 else f" {pi + 1}"
                    voice_names[voice_key] = f"{pname}{suffix}"
                for el in container.notesAndRests:
                    off_in_voice = (moff +
                                    Fraction(float(el.getOffsetInHierarchy(m)))
                                    .limit_denominator(100000))
                    dur = Fraction(float(el.quarterLength)).limit_denominator(100000)
                    tup = _read_tuplet(el)
                    tie_dir = el.tie.type if (el.isNote and el.tie) else \
                        ("continue" if el.isChord and el.tie and el.tie.type == "continue"
                         else (el.tie.type if el.isChord and el.tie else None))
                    pitches = []
                    if el.isChord:
                        pitches = [_pitch_from_m21(p) for p in el.notes]
                    elif el.isNote:
                        pitches = [_pitch_from_m21(el.pitch)]
                    kind = "rest" if el.isRest else ("chord" if el.isChord else "note")
                    raw_voices[voice_key].append({
                        "start": off_in_voice, "ql": dur, "tuplet": tup,
                        "tie": tie_dir, "kind": kind, "pitches": pitches,
                    })

    if not time_marks:
        time_marks = [TimeSignatureMark(QL(0), 4, 4)]
    time_marks.sort(key=lambda x: x.start)

    # Build one Voice per raw voice; merge tied chains into single events.
    # Each voice is a single sounding layer (monophonic per voice), so at most
    # one tie chain is open at a time.
    for key in voice_order:
        items = raw_voices[key]
        items.sort(key=lambda r: r["start"])
        events: list[SoundingEvent] = []
        open_ev: SoundingEvent | None = None
        cursor = QL(0)
        for r in items:
            start, dur = r["start"], r["ql"]
            tie = r["tie"]

            if tie in ("stop", "continue") and open_ev is not None and \
                    open_ev.end == start:
                open_ev.duration = Duration(
                    open_ev.duration.quarter_length + dur,
                    open_ev.duration.tuplet)
                if tie == "stop":
                    open_ev = None
                cursor = start + dur
                continue

            if start > cursor and (not events or events[-1].end <= cursor):
                events.append(SoundingEvent(
                    nid(), "rest", cursor, Duration(start - cursor), []))

            ev = SoundingEvent(nid(), r["kind"], start,
                               Duration(dur, r["tuplet"]), list(r["pitches"]))
            events.append(ev)
            cursor = start + dur
            open_ev = ev if tie == "start" else None

        vid = f"v{len(voices) + 1}"
        voices.append(Voice(vid, voice_names[key], events))

    # Normalize voice lengths: extend shorter voices with a trailing rest so
    # the score stays rectangular.
    end = max((e.end for v in voices for e in v.events), default=QL(8))
    for v in voices:
        vend = max((e.end for e in v.events), default=QL(0))
        if vend < end:
            v.events.append(SoundingEvent(nid(), "rest", vend,
                                          Duration(end - vend), []))

    score = Score(score_id, "Rebar score", voices, time_marks)
    return score


def _read_tuplet(el) -> object | None:
    from .model import TupletSpec
    tups = el.duration.tuplets if hasattr(el.duration, "tuplets") else []
    if not tups:
        return None
    t = tups[0]
    # unit type: derive from the written type of the duration component.
    unit = "eighth"
    comps = el.duration.components
    if comps:
        unit = comps[0].type
    return TupletSpec(int(t.numberNotesActual), int(t.numberNotesNormal), unit)


def _pitch_from_m21(p) -> Pitch:
    alter = int(p.accidental.alter) if p.accidental is not None else 0
    return Pitch(p.step, int(p.octave), alter)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_musicxml(score: Score) -> str:
    from .edits import validate_score
    validate_score(score)
    projection = notation.project(score)
    bars = projection["bars"]
    events = projection["events"]

    out_score = m21_stream.Score()
    part = m21_stream.Part()
    part.partName = score.voices[0].name if score.voices else "Part"

    # music21 objects keyed by our event ids.
    for bi, bar_w in enumerate(bars):
        m = m21_stream.Measure()
        m.number = bi + 1
        if bi == 0 or bar_w["timeLabel"] != bars[bi - 1]["timeLabel"] or \
                _mark_starts_here(score, bar_w):
            m.timeSignature = m21_meter.TimeSignature(
                f"{bar_w['beats']}/{bar_w['beatUnit']}")
        for vi, voice_w in enumerate(score.voices):
            voice_slices = bar_w["slicesByVoice"][vi]
            voice = m21_stream.Voice(id=str(vi + 1))
            for sl in voice_slices:
                for rn in sl["renderNotes"]:
                    el = _build_m21_element(sl, rn)
                    voice.insert(float(q(rn["beatInBar"])), el)
            if not voice_slices:
                # empty voice in a bar: whole-measure rest
                r = m21_note.Rest()
                r.quarterLength = float(q(bar_w["duration"]))
                r._style = None
                voice.insert(0, r)
            m.insert(0, voice)
        part.append(m)

    out_score.insert(0, part)
    raw = m21ToXml.GeneralObjectExporter().parse(out_score)
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw


def _mark_starts_here(score: Score, bar_w: dict) -> bool:
    start = q(bar_w["start"])
    return any(m.start == start for m in score.time_marks)


def _build_m21_element(slice_w: dict, rn: dict):
    ql = q(rn["ql"])
    if slice_w["kind"] == "rest":
        el = m21_note.Rest()
        el.quarterLength = float(ql)
        _set_type(el, rn)
        return el

    pitches = slice_w["pitches"]
    if slice_w["kind"] == "chord":
        notes = [m21_note.Note(_m21_name(p), quarterLength=float(ql))
                 for p in pitches]
        el = m21_note.Chord(notes)
    else:
        el = m21_note.Note(_m21_name(pitches[0]), quarterLength=float(ql))
    _set_type(el, rn)

    # ties: render note level (within and across bars)
    if rn["tieBefore"] or rn["tieAfter"]:
        if rn["tieBefore"] and rn["tieAfter"]:
            el.tie = m21_tie("continue")
        elif rn["tieBefore"]:
            el.tie = m21_tie("stop")
        else:
            el.tie = m21_tie("start")

    # tuplet
    if rn["tuplet"]:
        t = rn["tuplet"]
        tupl = m21_duration.Tuplet(t["actual"], t["normal"])
        tupl.setDurationType(t["unit"])
        tupl.bracket = True
        el.duration.appendTuplet(tupl)
    return el


def m21_tie(place: str):
    t = m21_tie_mod.Tie()
    t.type = place
    return t


def _set_type(el, rn: dict) -> None:
    """Force the displayed note type/dots to match projection's render note."""
    # quarterLength is already exact; type is inferred, but dots/type are
    # re-derived by music21 from quarterLength + tuplet, so nothing more is
    # needed for the in-scope values. Kept as a hook for future override.
    return None


def _m21_name(p: dict) -> str:
    acc = {0: "", 1: "#", 2: "##", -1: "-", -2: "--"}.get(p["alter"], "")
    return f"{p['step']}{acc}{p['octave']}"
