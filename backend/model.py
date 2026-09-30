"""Canonical musical model for score-rebar-web.

The model separates three layers explicitly:

* :class:`SoundingEvent` - one *sounding thing* at an absolute musical time
  within one voice ("the G4 that starts here and lasts 6.5 beats").  Moving or
  changing this is an actual musical edit.  It has a stable ``event_id`` that
  survives re-barring.
* Voice timelines - sounding events + rests tile each voice with no gaps or
  overlaps, measured in quarter-length beats at an infinite quarter-note
  tempo (4/4 or 3/4 changes only the *grid*, never the sound).
* :class:`NotationSlice` - a displayed fragment produced by cutting sounding
  events at barlines (and at tuplet group boundaries for notational grouping).
  One sounding event can be many slices joined by ties; selecting a slice must
  never create an extra sounding event.

Time is kept as exact rational ``Fraction`` quarter-length beats; the wire
format is a string "n/d" so the browser never sees float triplet drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

QL = Fraction  # quarter-length beat, rational


def q(value) -> QL:
    """Coerce int / float / 'n/d' string / Fraction to a Fraction beat."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(value).limit_denominator(100000)
    if isinstance(value, str):
        value = value.strip()
        if "/" in value:
            n, d = value.split("/", 1)
            return Fraction(int(n), int(d))
        return Fraction(int(value))
    raise TypeError(f"cannot interpret musical time from {value!r}")


def qs(value) -> str:
    """Serialize a beat as a fraction string for the wire."""
    f = q(value)
    return f"{f.numerator}/{f.denominator}"


# ---------------------------------------------------------------------------
# Pitch
# ---------------------------------------------------------------------------

STEP_TO_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
PC_TO_STEP = {v: k for k, v in STEP_TO_PC.items()}
# Accepted accidentals, in semitones, keyed by canonical name token.
ALTER = {"--": -2, "-": -1, "bb": -2, "b": -1, "#": 1, "##": 2, "n": 0, "": 0}


@dataclass(frozen=True)
class Pitch:
    """Concrete pitch: diatonic step + octave (C4 = middle C) + alteration."""

    step: str
    octave: int
    alter: int = 0

    @staticmethod
    def parse(name: str) -> "Pitch":
        name = name.strip()
        step = name[0].upper()
        if step not in STEP_TO_PC:
            raise ModelError(f"bad pitch step in {name!r}")
        i = 1
        token = ""
        while i < len(name) and name[i] not in "0123456789-":
            token += name[i]
            i += 1
        if token not in ALTER:
            raise ModelError(f"bad accidental in {name!r}")
        alter = ALTER[token]
        octave = int(name[i:])
        return Pitch(step, octave, alter)

    @property
    def midi(self) -> int:
        return (self.octave + 1) * 12 + STEP_TO_PC[self.step] + self.alter

    @property
    def name(self) -> str:
        acc = {0: "", 1: "#", 2: "##", -1: "-", -2: "--"}[self.alter]
        return f"{self.step}{acc}{self.octave}"

    def to_wire(self) -> dict:
        return {"step": self.step, "octave": self.octave, "alter": self.alter,
                "name": self.name, "midi": self.midi}

    @staticmethod
    def from_wire(d: dict) -> "Pitch":
        return Pitch(d["step"], int(d["octave"]), int(d.get("alter", 0)))


# ---------------------------------------------------------------------------
# Durations and tuplets
# ---------------------------------------------------------------------------

# Ordinary (non-tuplet) note head durations that engravers understand, with
# their quarter length: whole=4, half=2, quarter=1, eighth=1/2, ...
BASE_QL = {
    "whole": 4, "half": 2, "quarter": 1, "eighth": Fraction(1, 2),
    "16th": Fraction(1, 4), "32nd": Fraction(1, 8), "64th": Fraction(1, 16),
}
QL_TO_BASE = {v: k for k, v in BASE_QL.items()}


@dataclass(frozen=True)
class TupletSpec:
    """A tuplet group, e.g. 3:2 eighth notes (actual=3 normal=2).

    ``unit`` is the notated base type of one member ("eighth"); the sounding
    duration of one member is ``unit_ql * normal / actual``.
    """

    actual: int
    normal: int
    unit: str = "eighth"

    @property
    def unit_ql(self) -> QL:
        return BASE_QL[self.unit]

    @property
    def member_ql(self) -> QL:
        """Sounding quarter length of one member of the tuplet."""
        return self.unit_ql * self.normal / self.actual

    def to_wire(self) -> dict:
        return {"actual": self.actual, "normal": self.normal, "unit": self.unit,
                "memberQl": qs(self.member_ql),
                "groupQl": qs(self.unit_ql * self.normal)}

    @staticmethod
    def from_wire(d: dict) -> "TupletSpec":
        return TupletSpec(int(d["actual"]), int(d["normal"]), d.get("unit", "eighth"))


@dataclass
class Duration:
    """Sounding duration of an event, rational beats.

    A non-tuplet duration is decomposed for display into ordinary note values
    (with dots).  Tuplet events carry a :class:`TupletSpec` and the *membership
    count* is determined by ``quarterLength / member_ql``; the model requires a
    tuplet event to occupy an exact whole number of members of one tuplet
    group.
    """

    quarter_length: QL
    tuplet: TupletSpec | None = None

    def __post_init__(self):
        self.quarter_length = q(self.quarter_length)
        if self.quarter_length <= 0:
            raise ModelError("duration must be positive")
        if self.tuplet is not None:
            members = self.quarter_length / self.tuplet.member_ql
            if members.denominator != 1 or members <= 0:
                raise ModelError(
                    f"duration {self.quarter_length} is not a whole number of "
                    f"{self.tuplet.actual}:{self.tuplet.normal} "
                    f"{self.tuplet.unit} members")

    @property
    def tuplet_members(self) -> int:
        if self.tuplet is None:
            return 0
        return int(self.quarter_length / self.tuplet.member_ql)

    def to_wire(self) -> dict:
        d = {"quarterLength": qs(self.quarter_length)}
        if self.tuplet is not None:
            d["tuplet"] = self.tuplet.to_wire()
        return d

    @staticmethod
    def from_wire(d: dict) -> "Duration":
        return Duration(q(d["quarterLength"]),
                        TupletSpec.from_wire(d["tuplet"]) if d.get("tuplet") else None)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

EventKind = Literal["note", "chord", "rest"]


@dataclass
class SoundingEvent:
    """One musical utterance in one voice.

    A chord is one event with several pitches.  Ties never join two events in
    this layer: a held note is ONE long event; ties are generated when it is
    projected onto barlines.
    """

    event_id: str
    kind: EventKind
    start: QL
    duration: Duration
    pitches: list[Pitch] = field(default_factory=list)

    @property
    def end(self) -> QL:
        return self.start + self.duration.quarter_length

    def to_wire(self) -> dict:
        return {
            "eventId": self.event_id,
            "kind": self.kind,
            "start": qs(self.start),
            "end": qs(self.end),
            "duration": self.duration.to_wire(),
            "pitches": [p.to_wire() for p in self.pitches],
        }

    @staticmethod
    def from_wire(d: dict) -> "SoundingEvent":
        kind = d["kind"]
        pitches = [Pitch.from_wire(p) for p in d.get("pitches", [])]
        if kind in ("note", "chord") and not pitches:
            raise ModelError(f"{kind} event {d.get('eventId')} has no pitch")
        return SoundingEvent(d["eventId"], kind, q(d["start"]),
                             Duration.from_wire(d["duration"]), pitches)


# ---------------------------------------------------------------------------
# Voices, time signatures, score
# ---------------------------------------------------------------------------

@dataclass
class TimeSignatureMark:
    """A time signature taking effect at an absolute beat ``start``."""

    start: QL
    beats: int
    beat_unit: int

    @property
    def bar_length(self) -> QL:
        # Support simple meters with power-of-two beat units; numerator is
        # beats per bar.  (Scope: 4/4, 3/4, 2/4, 6/8-style compound is handled
        # by bar length = beats * 4/beat_unit which is also correct for 6/8.)
        return QL(self.beats * 4, self.beat_unit)

    @property
    def label(self) -> str:
        return f"{self.beats}/{self.beat_unit}"

    def to_wire(self) -> dict:
        return {"start": qs(self.start), "beats": self.beats,
                "beatUnit": self.beat_unit, "barLength": qs(self.bar_length),
                "label": self.label}

    @staticmethod
    def from_wire(d: dict) -> "TimeSignatureMark":
        return TimeSignatureMark(q(d["start"]), int(d["beats"]), int(d["beatUnit"]))


@dataclass
class Voice:
    voice_id: str
    name: str
    events: list[SoundingEvent] = field(default_factory=list)

    def sorted_events(self) -> list[SoundingEvent]:
        return sorted(self.events, key=lambda e: e.start)

    def find(self, event_id: str) -> SoundingEvent | None:
        for e in self.events:
            if e.event_id == event_id:
                return e
        return None


@dataclass
class Score:
    score_id: str
    title: str
    voices: list[Voice]
    time_marks: list[TimeSignatureMark]

    def initial_mark(self) -> TimeSignatureMark:
        return sorted(self.time_marks, key=lambda m: m.start)[0]

    def sorted_marks(self) -> list[TimeSignatureMark]:
        return sorted(self.time_marks, key=lambda m: m.start)

    def find_voice(self, voice_id: str) -> Voice | None:
        for v in self.voices:
            if v.voice_id == voice_id:
                return v
        return None

    def total_length(self) -> QL:
        end = QL(0)
        for v in self.voices:
            for e in v.events:
                end = max(end, e.end)
        return end

    # ---- (de)serialization -------------------------------------------------

    def to_wire(self) -> dict:
        return {
            "scoreId": self.score_id,
            "title": self.title,
            "voices": [
                {"voiceId": v.voice_id, "name": v.name,
                 "events": [e.to_wire() for e in v.sorted_events()]}
                for v in self.voices
            ],
            "timeSignatures": [m.to_wire() for m in self.sorted_marks()],
            "totalLength": qs(self.total_length()),
        }

    @staticmethod
    def from_wire(d: dict) -> "Score":
        voices = [Voice(v["voiceId"], v.get("name", v["voiceId"]),
                        [SoundingEvent.from_wire(e) for e in v.get("events", [])])
                  for v in d["voices"]]
        marks = [TimeSignatureMark.from_wire(m) for m in d.get("timeSignatures", [])]
        return Score(d["scoreId"], d.get("title", "Score"), voices, marks)


class ModelError(ValueError):
    """Raised for malformed model content or rejected edits."""
