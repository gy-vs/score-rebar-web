"""Projection of the canonical model onto *displayed* notation.

The sounding model is bar-free.  This module produces what is drawn:

* bar boundaries derived from the time-signature marks (the rebar grid);
* render notes - sounding events cut at barlines AND cut into engraveable note
  values that fit inside a bar, chained with ties;
* tuplet group metadata (3:2 etc.) with bracket start/continue/stop roles;
* visible rests.

A render note is one notehead/rest glyph.  A *slice* is the group of render
notes inside one bar belonging to one sounding event - the selectable visual
fragment.  Slice ids are deterministic (``eventId#start=end``) and always
reference the underlying ``eventId``; adding a barline therefore adds slices
but never sounding events.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .model import BASE_QL, Pitch, QL, Score, SoundingEvent, q, qs


# ---------------------------------------------------------------------------
# Bar grid
# ---------------------------------------------------------------------------

@dataclass
class Bar:
    index: int
    start: QL
    duration: QL
    time_label: str
    time_beats: int
    time_beat_unit: int

    @property
    def end(self) -> QL:
        return self.start + self.duration

    def to_wire(self) -> dict:
        return {"index": self.index, "start": qs(self.start),
                "end": qs(self.end), "duration": qs(self.duration),
                "timeLabel": self.time_label, "beats": self.time_beats,
                "beatUnit": self.time_beat_unit}


def build_bars(score: Score) -> list[Bar]:
    """All bars covering the score. The last bar is rendered full length."""
    marks = score.sorted_marks()
    length = score.total_length()
    bars: list[Bar] = []
    cursor = QL(0)
    mi = 0
    mark_idx = 0
    while True:
        while mark_idx + 1 < len(marks) and marks[mark_idx + 1].start <= cursor:
            mark_idx += 1
        mark = marks[mark_idx]
        bars.append(Bar(mi, cursor, mark.bar_length, mark.label,
                        mark.beats, mark.beat_unit))
        cursor += mark.bar_length
        mi += 1
        if cursor >= length:
            break
    return bars


# ---------------------------------------------------------------------------
# Render-note generation
# ---------------------------------------------------------------------------

# Candidate plain note values, longest first.  Dotted variants are listed
# first but only chosen when they do not straddle a stronger beat boundary
# (see ``_pick_plain``), so 3 beats at the start of a 3/4 or 4/4 bar is a
# dotted half, while a segment ending on the barline is never over-filled.
_PLAIN_CANDIDATES: list[tuple[str, int, QL]] = []
for _name, _base in sorted(BASE_QL.items(), key=lambda kv: -kv[1]):
    _PLAIN_CANDIDATES.append((_name, 2, _base * Fraction(7, 4)))
    _PLAIN_CANDIDATES.append((_name, 1, _base * Fraction(3, 2)))
    _PLAIN_CANDIDATES.append((_name, 0, _base))


@dataclass
class RenderNote:
    type: str           # whole/half/quarter/eighth/...
    dots: int
    ql: QL              # SOUNDING quarter length (1/3 for a triplet eighth)
    notated_ql: QL      # written note value length (1/2 for a triplet eighth)
    beat_in_bar: QL
    is_rest: bool
    tuplet: dict | None
    tie_before: bool    # tie arriving from previous render note
    tie_after: bool     # tie leaving to next render note

    def to_wire(self) -> dict:
        return {"type": self.type, "dots": self.dots,
                "ql": qs(self.ql), "notatedQl": qs(self.notated_ql),
                "beatInBar": qs(self.beat_in_bar),
                "rest": self.is_rest, "tuplet": self.tuplet,
                "tieBefore": self.tie_before, "tieAfter": self.tie_after}


def render_voice_in_bar(voice_events: list[SoundingEvent], bar: Bar) -> list:
    """Lay one voice timeline across one bar, returning (event, [RenderNote]).

    Output is one entry per sounding event that appears in this bar: the
    *slice* content for this bar.
    """
    time = bar.start
    bar_end = bar.end
    # Pre-compute tuplet bracket roles across CONSECUTIVE tuplet events in the
    # voice timeline: adjacent tuplet members share one bracket group even
    # though they are distinct sounding events (the classic 3:2 triplet).
    bracket_role = _bracket_roles(voice_events)
    slices: list[SoundingEvent] = []
    notes_by_event: dict[str, list[RenderNote]] = {}

    evs = voice_events
    i = 0
    while i < len(evs) and evs[i].end <= time:
        i += 1

    while time < bar_end:
        if i >= len(evs):
            # pad the remainder of the final bar with visible rest glyph(s)
            rem = bar_end - time
            ntype, ndots, nql = _pick_plain(rem, time - bar.start, bar)
            while True:
                rn = RenderNote(ntype, ndots, nql, nql, time - bar.start,
                                True, None, False, False)
                notes_by_event.setdefault(REST_KEY, []).append(rn)
                time += nql
                if time >= bar_end:
                    break
                ntype, ndots, nql = _pick_plain(
                    bar_end - time, time - bar.start, bar)
            break

        ev = evs[i]
        if ev.start > time:
            raise ValueError(f"gap at {time} in voice layout")
        event_rem = ev.end - time
        first_note_of_event = (time == ev.start)

        if ev.duration.tuplet is not None:
            member_ql, tup_meta = _tuplet_step(ev, time)
            note_ql = member_ql
            ntype, ndots = ev.duration.tuplet.unit, 0
            notated_ql = ev.duration.tuplet.unit_ql
            tuplet = {**tup_meta, "bracketRole": bracket_role.get(ev.event_id, "start")}
        else:
            ntype, ndots, note_ql = _pick_plain(
                min(event_rem, bar_end - time), time - bar.start, bar)
            notated_ql = note_ql
            tuplet = None

        tie_before = not first_note_of_event
        tie_after = (time + note_ql) < ev.end

        rn = RenderNote(ntype, ndots, note_ql, notated_ql, time - bar.start,
                        ev.kind == "rest", tuplet, tie_before, tie_after)
        notes_by_event.setdefault(ev.event_id, []).append(rn)
        if not slices or slices[-1] is not ev:
            slices.append(ev)
        time += note_ql
        if time >= ev.end:
            i += 1

    return slices, notes_by_event


REST_KEY = "__trailing_rest__"


def _bracket_roles(events: list[SoundingEvent]) -> dict[str, str]:
    """Map tuplet event ids to start/continue/stop of a shared bracket.

    Consecutive events with the same tuplet spec (actual/normal/unit) that are
    contiguous in time form one notated tuplet group.
    """
    roles: dict[str, str] = {}
    grp: list[SoundingEvent] = []

    def flush(grp):
        if not grp:
            return
        for k, e in enumerate(grp):
            roles[e.event_id] = ("start" if k == 0 else
                                 "stop" if k == len(grp) - 1 else "continue")

    for ev in events:
        tup = ev.duration.tuplet
        if tup is None:
            flush(grp); grp = []
            continue
        if grp:
            prev = grp[-1]
            pt = prev.duration.tuplet
            same = (pt.actual == tup.actual and pt.normal == tup.normal
                    and pt.unit == tup.unit and prev.end == ev.start)
            if not same:
                flush(grp); grp = []
        grp.append(ev)
    flush(grp)
    return roles


def _pick_plain(budget: QL, beat_in_bar: QL, bar: Bar) -> tuple[str, int, QL]:
    """Largest engraveable note value fitting ``budget``.

    A dotted value is used only when it ends on a beat boundary; otherwise a
    plain value ending on a beat is preferred, falling back to the largest
    plain value that fits.  This yields dotted-half for 3 beats at bar start
    and plain values across barline-cut segments.
    """
    grid = QL(4, bar.time_beat_unit)
    fallback = None
    for name, dots, ql in _PLAIN_CANDIDATES:
        if ql > budget:
            continue
        if fallback is None:
            fallback = (name, dots, ql)
        end_beat = beat_in_bar + ql
        if dots == 0 and (end_beat / grid).denominator == 1:
            return name, dots, ql
        if dots > 0 and (end_beat / grid).denominator == 1:
            # candidate dotted value landing on a beat: use it unless an
            # equally good plain candidate exists later
            rest_of_budget = budget - ql
            if rest_of_budget == 0:
                return name, dots, ql
            # If a plain candidate of the same span ending on beat exists it
            # will be caught by the dots==0 branch on a later iteration only
            # when its ql is smaller; prefer the longer dotted one here.
            return name, dots, ql
    if fallback:
        return fallback
    raise ValueError(f"no note value fits in {budget}")


def _tuplet_step(ev: SoundingEvent, time: QL):
    tup = ev.duration.tuplet
    member = tup.member_ql
    idx = (time - ev.start) / member
    if idx.denominator != 1:
        raise ValueError("render cursor aligned inside a tuplet member")
    meta = {"actual": tup.actual, "normal": tup.normal, "unit": tup.unit,
            "bracketNumber": 1}
    return member, meta


def _event_slice_wire(voice_id: str, bar: Bar, event_id: str, kind: str,
                      pitches, notes: list[RenderNote]) -> dict:
    seg_start = bar.start + notes[0].beat_in_bar
    seg_end = bar.start + notes[-1].beat_in_bar + notes[-1].ql
    return {
        "sliceId": f"{event_id}#{qs(seg_start)}={qs(seg_end)}",
        "eventId": event_id,
        "voiceId": voice_id,
        "barIndex": bar.index,
        "beatInBar": qs(notes[0].beat_in_bar),
        "segStart": qs(seg_start),
        "segEnd": qs(seg_end),
        "segLength": qs(seg_end - seg_start),
        "kind": kind,
        "pitches": [p.to_wire() for p in pitches],
        "tieIn": notes[0].tie_before,
        "tieOut": notes[-1].tie_after,
        "renderNotes": [rn.to_wire() for rn in notes],
    }


# ---------------------------------------------------------------------------
# Full projection
# ---------------------------------------------------------------------------

def project(score: Score) -> dict:
    bars = build_bars(score)
    voice_ids = [v.voice_id for v in score.voices]
    events_timeline = {v.voice_id: v.sorted_events() for v in score.voices}
    # Determine all slice ids per event (one per bar the event appears in).
    bars_out = []
    slice_index: dict[str, dict] = {}

    for bar in bars:
        voices_bars = []
        for v in score.voices:
            slices, notes_by_event = render_voice_in_bar(
                events_timeline[v.voice_id], bar)
            slice_wires = []
            for ev in slices:
                wire = _event_slice_wire(v.voice_id, bar, ev.event_id, ev.kind,
                                         ev.pitches,
                                         notes_by_event[ev.event_id])
                slice_wires.append(wire)
                slice_index[wire["sliceId"]] = wire
            if REST_KEY in notes_by_event:
                notes = notes_by_event[REST_KEY]
                seg_start = bar.start + notes[0].beat_in_bar
                seg_end = bar.start + notes[-1].beat_in_bar + notes[-1].ql
                rid = f"__fillrest__b{bar.index}:{v.voice_id}"
                wire = {
                    "sliceId": rid, "eventId": rid, "voiceId": v.voice_id,
                    "barIndex": bar.index,
                    "beatInBar": qs(notes[0].beat_in_bar),
                    "segStart": qs(seg_start), "segEnd": qs(seg_end),
                    "segLength": qs(seg_end - seg_start),
                    "kind": "rest", "pitches": [],
                    "tieIn": False, "tieOut": False,
                    "renderNotes": [rn.to_wire() for rn in notes],
                    "fillRest": True,
                }
                slice_wires.append(wire)
                slice_index[rid] = wire
            voices_bars.append(slice_wires)
        bars_out.append({**bar.to_wire(), "slicesByVoice": voices_bars})

    # Event summary with ordered slice ids.
    events_out = {}
    for v in score.voices:
        for ev in v.events:
            ids = [sid for sid, w in slice_index.items()
                   if w["eventId"] == ev.event_id and w["voiceId"] == v.voice_id]
            ids.sort(key=lambda s: q(slice_index[s]["segStart"]))
            events_out[ev.event_id] = {
                **ev.to_wire(),
                "voiceId": v.voice_id,
                "sliceIds": ids,
            }

    return {
        "bars": bars_out,
        "voices": [
            {"voiceId": v.voice_id, "name": v.name, "clef": _voice_clef(v)}
            for v in score.voices
        ],
        "events": events_out,
    }


def _voice_clef(voice) -> str:
    """Treble unless the voice sits low (median notated pitch <= middle C)."""
    midis = sorted(p.midi for e in voice.events if e.kind != "rest"
                   for p in e.pitches)
    if not midis:
        return "treble"
    median = midis[len(midis) // 2]
    return "bass" if median <= Pitch.parse("C4").midi else "treble"
