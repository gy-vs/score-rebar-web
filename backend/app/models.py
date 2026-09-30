"""Canonical score model: sounded events on independent voice timelines.

The model deliberately contains *no measures*.  A voice is a non-overlapping
timeline of items (note / chord / rest) whose times are absolute quarter-note
offsets from the beginning of the score.  Measures, ties and tuplet brackets
are derived views produced by ``layout.py``.

Ties are therefore never stored: if one sounded event spans a barline the
layout splits it into two fragments joined by a derived tie.  Changing meter
only recomputes that view; the sounded time of every event is untouched.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from fractions import Fraction
from typing import Optional

from .rational import as_q, q_json


def new_id(prefix: str) -> str:
    # time prefix keeps ids roughly human-sortable, uuid keeps them unique
    return f"{prefix}_{int(time.time() * 1000):x}_{uuid.uuid4().hex[:8]}"


# ---------- pitch ----------

@dataclass
class Pitch:
    step: str            # C D E F G A B
    alter: int = 0       # semitones: -1 flat, 0 natural, 1 sharp
    octave: int = 4

    def name(self) -> str:
        acc = {1: "#", -1: "b", 0: ""}.get(self.alter, str(self.alter))
        return f"{self.step}{acc}{self.octave}"

    def to_dict(self) -> dict:
        return {"step": self.step, "alter": self.alter, "octave": self.octave}

    @classmethod
    def from_dict(cls, d: dict) -> "Pitch":
        return cls(step=d["step"], alter=int(d.get("alter", 0)), octave=int(d["octave"]))


# ---------- tuplet groups ----------

@dataclass
class TupletGroup:
    """A contiguous group, e.g. 3 notes in the time of 2.

    Members are regular voice items; their ``tuplet_group_id`` points here.
    The *display* bracket boundaries are derived by the layout when a group
    is split across a barline.
    """
    id: str
    actual_notes: int = 3
    normal_notes: int = 2
    member_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TupletGroup":
        return cls(
            id=d["id"],
            actual_notes=int(d.get("actual_notes", 3)),
            normal_notes=int(d.get("normal_notes", 2)),
            member_ids=list(d.get("member_ids", [])),
        )


# ---------- voice timeline items (sounded events) ----------

@dataclass
class VoiceItem:
    id: str
    kind: str                        # 'note' | 'chord' | 'rest'
    onset: Fraction
    duration: Fraction
    pitches: list[Pitch] = field(default_factory=list)
    tuplet_group_id: Optional[str] = None

    @property
    def end(self) -> Fraction:
        return self.onset + self.duration

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "onset": q_json(self.onset),
            "duration": q_json(self.duration),
            "pitches": [p.to_dict() for p in self.pitches],
            "tupletGroupId": self.tuplet_group_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VoiceItem":
        kind = d["kind"]
        pitches = [Pitch.from_dict(p) for p in d.get("pitches", [])]
        return cls(
            id=d["id"],
            kind=kind,
            onset=as_q(d["onset"]),
            duration=as_q(d["duration"]),
            pitches=pitches,
            tuplet_group_id=d.get("tupletGroupId"),
        )


@dataclass
class Voice:
    id: str
    name: str
    items: list[VoiceItem] = field(default_factory=list)

    def sorted_items(self) -> list[VoiceItem]:
        return sorted(self.items, key=lambda it: (it.onset, it.id))

    def get(self, item_id: str) -> Optional[VoiceItem]:
        for it in self.items:
            if it.id == item_id:
                return it
        return None

    def end(self) -> Fraction:
        return max((it.end for it in self.items), default=Fraction(0))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "items": [it.to_dict() for it in self.sorted_items()],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Voice":
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            items=[VoiceItem.from_dict(x) for x in d.get("items", [])],
        )


# ---------- meter regions ----------

@dataclass
class MeterRegion:
    """Meter in force from ``onset`` (absolute QL) until the next region."""
    onset: Fraction
    beats: int
    beat_unit: int

    @property
    def bar_length(self) -> Fraction:
        return Fraction(4, self.beat_unit) * self.beats

    def label(self) -> str:
        return f"{self.beats}/{self.beat_unit}"

    def to_dict(self) -> dict:
        return {"onset": q_json(self.onset), "beats": self.beats, "beatUnit": self.beat_unit}

    @classmethod
    def from_dict(cls, d: dict) -> "MeterRegion":
        return cls(onset=as_q(d["onset"]), beats=int(d["beats"]), beat_unit=int(d["beatUnit"]))


# ---------- score ----------

@dataclass
class Score:
    id: str
    title: str
    version: int
    voices: list[Voice]
    meters: list[MeterRegion]
    tuplet_groups: list[TupletGroup] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())

    # ----- helpers -----
    def get_voice(self, voice_id: str) -> Optional[Voice]:
        for v in self.voices:
            if v.id == voice_id:
                return v
        return None

    def get_item(self, item_id: str) -> tuple[Voice, VoiceItem] | None:
        for v in self.voices:
            it = v.get(item_id)
            if it is not None:
                return v, it
        return None

    def tuplet_group_of(self, item: VoiceItem) -> Optional[TupletGroup]:
        if not item.tuplet_group_id:
            return None
        for g in self.tuplet_groups:
            if g.id == item.tuplet_group_id:
                return g
        return None

    def length(self) -> Fraction:
        """Total sounding span (QL). Trailing rests are stored explicitly."""
        return max((v.end() for v in self.voices), default=Fraction(0))

    def meter_at(self, onset: Fraction) -> MeterRegion:
        active = self.meters[0]
        for m in self.meters:
            if m.onset <= onset:
                active = m
            else:
                break
        return active

    def bump(self) -> None:
        self.version += 1
        self.updated_at = time.time()

    # ----- (de)serialisation -----
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "version": self.version,
            "voices": [v.to_dict() for v in self.voices],
            "meters": [m.to_dict() for m in self.meters],
            "tupletGroups": [g.to_dict() for g in self.tuplet_groups],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Score":
        return cls(
            id=d["id"],
            title=d.get("title", "Untitled"),
            version=int(d.get("version", 1)),
            voices=[Voice.from_dict(v) for v in d["voices"]],
            meters=[MeterRegion.from_dict(m) for m in d["meters"]],
            tuplet_groups=[TupletGroup.from_dict(g) for g in d.get("tupletGroups", [])],
            created_at=float(d.get("createdAt", time.time())),
            updated_at=float(d.get("updatedAt", time.time())),
        )


# ---------- seed: the exact fragment described in the brief ----------

def seed_score() -> Score:
    """Two measures of 4/4, two voices.

    Upper voice: eighth-triplet G4 A4 B4, then G4 sounding 2.5 QL so it crosses
    into measure 2 (where it is displayed as a tied half + eighth).
    Lower voice: one C3 per measure.
    """
    v_up = Voice(id="v1", name="上声部")
    v_down = Voice(id="v2", name="下声部")

    g_id = new_id("tg")
    members: list[VoiceItem] = []
    step = 0
    for step_name in ("G", "A", "B"):
        members.append(VoiceItem(
            id=new_id("ev"), kind="note",
            onset=Fraction(step * 2, 3), duration=Fraction(2, 3),
            pitches=[Pitch(step=step_name, octave=4)],
            tuplet_group_id=g_id,
        ))
        step += 1
    v_up.items.extend(members)
    v_up.items.append(VoiceItem(
        id=new_id("ev"), kind="note",
        onset=Fraction(2), duration=Fraction(5, 2),
        pitches=[Pitch(step="G", octave=4)],
    ))
    v_up.items.append(VoiceItem(
        id=new_id("ev"), kind="rest",
        onset=Fraction(9, 2), duration=Fraction(7, 2),
    ))
    v_down.items.append(VoiceItem(
        id=new_id("ev"), kind="note", onset=Fraction(0), duration=4,
        pitches=[Pitch(step="C", octave=3)],
    ))
    v_down.items.append(VoiceItem(
        id=new_id("ev"), kind="note", onset=4, duration=4,
        pitches=[Pitch(step="C", octave=3)],
    ))

    score = Score(
        id="seed", title="素材片段（4/4 两小节）", version=1,
        voices=[v_up, v_down],
        meters=[MeterRegion(onset=Fraction(0), beats=4, beat_unit=4)],
        tuplet_groups=[TupletGroup(id=g_id, actual_notes=3, normal_notes=2,
                                   member_ids=[m.id for m in members])],
    )
    return score
