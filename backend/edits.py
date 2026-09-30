"""Invariant checks and edit operations over the canonical score.

The central invariant for every voice: the sounding events (including rests)
must *tile* the voice from beat 0 to the voice end with no gaps and no
overlaps.  Every edit is validated against this invariant; an edit that breaks
it is rejected with a precise reason instead of silently corrupting the part.
"""
from __future__ import annotations

from fractions import Fraction

from .model import (
    Duration, ModelError, Pitch, QL, Score, SoundingEvent, TimeSignatureMark,
    Voice, q,
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_voice(voice: Voice) -> None:
    events = voice.sorted_events()
    if not events:
        raise ModelError(f"voice {voice.voice_id!r} is empty")
    cursor = QL(0)
    for e in events:
        if e.start != cursor:
            if e.start > cursor:
                raise ModelError(
                    f"voice {voice.voice_id}: gap of {e.start - cursor} beats "
                    f"before event {e.event_id} (start {e.start})")
            raise ModelError(
                f"voice {voice.voice_id}: event {e.event_id} at {e.start} "
                f"overlaps previous content ending at {cursor}")
        if e.kind == "note" and len(e.pitches) != 1:
            raise ModelError(f"note event {e.event_id} must have exactly one pitch")
        if e.kind == "chord" and len(e.pitches) < 2:
            raise ModelError(f"chord event {e.event_id} needs >= 2 pitches")
        if e.kind == "rest" and e.pitches:
            raise ModelError(f"rest event {e.event_id} cannot carry pitches")
        cursor = e.end
    if cursor <= 0:
        raise ModelError(f"voice {voice.voice_id} has zero length")


def validate_score(score: Score) -> None:
    if not score.voices:
        raise ModelError("score has no voices")
    marks = score.sorted_marks()
    if not marks or marks[0].start != 0:
        raise ModelError("score must have a time signature starting at beat 0")
    for m in marks:
        if m.beats <= 0 or m.beat_unit <= 0:
            raise ModelError(f"invalid time signature {m.label}")
    voice_ends = set()
    for v in score.voices:
        merge_adjacent_rests(v)
        validate_voice(v)
        voice_ends.add(v.sorted_events()[-1].end)
    if len(voice_ends) != 1:
        raise ModelError(
            f"voices end at different times: {sorted(map(str, voice_ends))}")


def merge_adjacent_rests(voice: Voice) -> None:
    """Collapse consecutive rests into one (they carry no identity)."""
    events = voice.sorted_events()
    merged: list[SoundingEvent] = []
    for e in events:
        if (e.kind == "rest" and merged and merged[-1].kind == "rest"
                and merged[-1].end == e.start):
            prev = merged[-1]
            prev.duration = Duration(
                prev.duration.quarter_length + e.duration.quarter_length)
        else:
            merged.append(e)
    voice.events = merged


# ---------------------------------------------------------------------------
# Edits
# ---------------------------------------------------------------------------

def _rest_matching(events: list[SoundingEvent], start: QL, length: QL,
                   id_factory) -> SoundingEvent:
    return SoundingEvent(id_factory(), "rest", start, Duration(length), [])


def change_pitch(score: Score, voice_id: str, event_id: str,
                 pitches: list[str]) -> None:
    """Change the pitch content of a note/chord. Kind and time are untouched."""
    voice = _require_voice(score, voice_id)
    ev = voice.find(event_id)
    if ev is None:
        raise ModelError(f"no event {event_id} in voice {voice_id}")
    parsed = [Pitch.parse(p) for p in pitches]
    if not parsed:
        raise ModelError("an event must have at least one pitch")
    # Sort pitches high->low for canonical order (chord display order).
    parsed.sort(key=lambda p: -p.midi)
    ev.pitches = parsed
    ev.kind = "note" if len(parsed) == 1 else "chord"
    validate_score(score)


def to_rest(score: Score, voice_id: str, event_id: str) -> None:
    voice = _require_voice(score, voice_id)
    ev = voice.find(event_id)
    if ev is None:
        raise ModelError(f"no event {event_id} in voice {voice_id}")
    ev.kind = "rest"
    ev.pitches = []
    validate_score(score)


def change_duration(score: Score, voice_id: str, event_id: str,
                    new_duration: Duration) -> None:
    """Resize an event, adjusting the neighboring rest/event to keep tiling.

    The voice end is fixed: growing one event shrinks the *next* event if that
    next event is a rest, or the previous rest when shrinking leaves a gap.
    If the neighboring content is a sounding note, the resize is rejected so a
    note is never silently overwritten.
    """
    voice = _require_voice(score, voice_id)
    events = voice.sorted_events()
    idx = next((i for i, e in enumerate(events) if e.event_id == event_id), -1)
    if idx < 0:
        raise ModelError(f"no event {event_id} in voice {voice_id}")
    ev = events[idx]
    delta = new_duration.quarter_length - ev.duration.quarter_length
    if delta == 0:
        return

    nxt = events[idx + 1] if idx + 1 < len(events) else None
    prv = events[idx - 1] if idx > 0 else None

    if delta > 0:
        if nxt is None:
            raise ModelError("cannot lengthen the final event past the end")
        if nxt.kind != "rest" or nxt.duration.quarter_length < delta:
            raise ModelError(
                f"lengthening {event_id} by {delta} beats would overwrite a "
                f"sounding event (next is {nxt.event_id}); move it instead")
        if nxt.duration.quarter_length == delta:
            voice.events.remove(nxt)
        else:
            nxt.start += delta
            nxt.duration = Duration(nxt.duration.quarter_length - delta,
                                    nxt.duration.tuplet)
    else:
        # shrinking: insert/extend a rest directly after the event
        gap = -delta
        if nxt is not None and nxt.kind == "rest":
            nxt.start -= gap
            nxt.duration = Duration(nxt.duration.quarter_length + gap,
                                    nxt.duration.tuplet)
        else:
            rest = SoundingEvent(_new_id(), "rest", ev.end, Duration(gap), [])
            # everything after shifts right by `gap`
            for e in events[idx + 1:]:
                e.start += gap
            voice.events.append(rest)
    ev.duration = new_duration
    validate_score(score)


def move_event(score: Score, voice_id: str, event_id: str, new_start: QL,
               new_duration: Duration | None = None) -> None:
    """Move a sounding event to another musical time in the SAME voice.

    The region the event vacates is filled with a rest; any rest at the
    destination is absorbed.  Landing on / crossing sounding events is rejected.
    A rest cannot be moved (it has no musical identity to preserve).
    """
    new_start = q(new_start)
    voice = _require_voice(score, voice_id)
    events = voice.sorted_events()
    ev = next((e for e in events if e.event_id == event_id), None)
    if ev is None:
        raise ModelError(f"no event {event_id} in voice {voice_id}")
    if ev.kind == "rest":
        raise ModelError("rests are implicit space; they cannot be moved")
    if new_start == ev.start:
        if new_duration is not None:
            change_duration(score, voice_id, event_id, new_duration)
        return
    dur = new_duration.quarter_length if new_duration else ev.duration.quarter_length
    if dur <= 0:
        raise ModelError("duration must be positive")
    voice_end = events[-1].end

    old_start, old_end = ev.start, ev.end
    new_end = new_start + dur

    if new_start < 0 or new_end > voice_end:
        raise ModelError(
            f"destination {new_start}..{new_end} lies outside the voice "
            f"(0..{voice_end})")

    # The event itself is removed from collision checking; then the *new*
    # region must contain only rests, and the vacated region is filled.
    others = [e for e in events if e is not ev]
    for other in others:
        if other.kind == "rest":
            continue
        if new_start < other.end and other.start < new_end:
            raise ModelError(
                f"destination {new_start}..{new_end} collides with sounding "
                f"event {other.event_id} at {other.start}..{other.end}")

    # Recompose: drop target event and any rests intersecting [old] or [new],
    # then re-tile with the moved event and fresh rests.
    kept = [e for e in others if e.kind != "rest"]
    moved = SoundingEvent(ev.event_id, ev.kind, new_start,
                          Duration(dur, dur_spec(new_duration, ev)), ev.pitches)
    kept.append(moved)
    kept.sort(key=lambda e: e.start)
    # ensure sounding events among themselves do not overlap
    for a, b in zip(kept, kept[1:]):
        if a.end > b.start:
            raise ModelError(
                f"sounding events {a.event_id} and {b.event_id} would overlap")
    voice.events = _retile_with_rests(kept, voice_end)
    validate_score(score)


def insert_event(score: Score, voice_id: str, kind: str, start: QL,
                 duration: Duration, pitches: list[Pitch], id_factory=None) -> str:
    """Insert a new sounding event; destination must be entirely within rests."""
    voice = _require_voice(score, voice_id)
    start = q(start)
    end = start + duration.quarter_length
    for e in voice.events:
        if e.kind == "rest":
            continue
        if start < e.end and e.start < end:
            raise ModelError(
                f"cannot insert at {start}..{end}: collides with {e.event_id}")
    new_id = id_factory() if id_factory else _new_id()
    ev = SoundingEvent(new_id, kind, start, duration, pitches)
    voice.events.append(ev)
    voice.events = _retile_with_rests(
        [e for e in voice.events if e.kind != "rest"],
        voice.sorted_events()[-1].end, id_factory)
    validate_score(score)
    return new_id


def set_time_signature(score: Score, start: QL, beats: int, beat_unit: int) -> None:
    """Set/change the meter from ``start`` onward.

    Sounding times never move: only the bar grid changes.  Any existing mark at
    or after ``start`` is replaced (one meter from this point forward).  Voices
    are then padded with trailing rests up to the end of the final bar so the
    score keeps full bars (this is the content that round-trips).
    """
    start = q(start)
    marks = [m for m in score.time_marks if m.start < start]
    marks.append(TimeSignatureMark(start, beats, beat_unit))
    score.time_marks = marks
    _pad_to_grid(score)
    validate_score(score)


def _pad_to_grid(score: Score) -> None:
    """Extend every voice with trailing rests to the last bar boundary."""
    from .notation import build_bars
    grid_end = build_bars(score)[-1].end
    counter = [900000]

    def rid():
        counter[0] += 1
        return f"pad-{counter[0]}"

    for v in score.voices:
        events = v.sorted_events()
        end = events[-1].end if events else QL(0)
        if end < grid_end:
            v.events.append(SoundingEvent(rid(), "rest", end,
                                          Duration(grid_end - end), []))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def dur_spec(new_duration: Duration | None, ev: SoundingEvent):
    if new_duration is not None:
        return new_duration.tuplet
    return ev.duration.tuplet


def _retile_with_rests(sounding: list[SoundingEvent], voice_end: QL,
                       id_factory=None) -> list[SoundingEvent]:
    """Given non-overlapping sounding events, return a fully tiled list with rests."""
    id_factory = id_factory or _new_id
    out: list[SoundingEvent] = []
    cursor = QL(0)
    for e in sorted(sounding, key=lambda x: x.start):
        if e.start > cursor:
            out.append(SoundingEvent(id_factory(), "rest", cursor,
                                     Duration(e.start - cursor), []))
        out.append(e)
        cursor = e.end
    if cursor < voice_end:
        out.append(SoundingEvent(id_factory(), "rest", cursor,
                                 Duration(voice_end - cursor), []))
    return out


_counter = [0]


def _new_id() -> str:
    _counter[0] += 1
    return f"rest-{_counter[0]:04d}"


def _require_voice(score: Score, voice_id: str) -> Voice:
    v = score.find_voice(voice_id)
    if v is None:
        raise ModelError(f"no such voice {voice_id!r}")
    return v
