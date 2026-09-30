"""Rebarring / layout derivation.

Input: :class:`Score` — absolute voice timelines + meter regions (no measures).
Output: measures → per-voice :class:`Fragment` lists → notation tokens.

The output is a *view*: it is never edited and never persisted.  Changing a
meter region changes this view only; sounded event onset/duration are fixed,
which is the property the editor relies on ("改拍号不改声音时间").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

from .models import MeterRegion, Score, Voice, VoiceItem
from .rational import q_json


# ---------- output dataclasses ----------

@dataclass
class Token:
    id: str
    code: str                 # vexflow-ish duration code: w h q e 16 32
    dots: int = 0
    tie_in: bool = False
    tie_out: bool = False
    whole_measure: bool = False
    tuplet_group_id: Optional[str] = None

    def to_dict(self, seq: int) -> dict:
        return {
            "id": self.id,
            "seq": seq,
            "duration": self.code,
            "dots": self.dots,
            "tieIn": self.tie_in,
            "tieOut": self.tie_out,
            "wholeMeasure": self.whole_measure,
            "tupletGroupId": self.tuplet_group_id,
        }


@dataclass
class Fragment:
    """The visible slice of one sounded event inside one measure."""
    item_id: str
    kind: str
    onset: Fraction                    # absolute QL of this slice
    duration: Fraction
    local_onset: Fraction              # QL from measure start
    head: bool                         # slice starts at the event's onset
    tail: bool                         # slice ends at the event's end
    pitches: list = field(default_factory=list)
    tuplet_group_id: Optional[str] = None
    tokens: list[Token] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "itemId": self.item_id,
            "kind": self.kind,
            "onset": q_json(self.onset),
            "duration": q_json(self.duration),
            "localOnset": q_json(self.local_onset),
            "head": self.head,
            "tail": self.tail,
            "pitches": [p.to_dict() for p in self.pitches],
            "tupletGroupId": self.tuplet_group_id,
            "tokens": [t.to_dict(i) for i, t in enumerate(self.tokens)],
        }


@dataclass
class MeasureView:
    index: int
    onset: Fraction
    duration: Fraction
    beats: int
    beat_unit: int

    def to_dict(self, voices: dict[str, list[Fragment]]) -> dict:
        return {
            "index": self.index,
            "onset": q_json(self.onset),
            "duration": q_json(self.duration),
            "beats": self.beats,
            "beatUnit": self.beat_unit,
            "voices": {
                vid: [f.to_dict() for f in frags]
                for vid, frags in voices.items()
            },
        }


# ---------- measure grid ----------

def measure_grid(score: Score) -> list[tuple[Fraction, Fraction, MeterRegion]]:
    """Return [(measure_onset, bar_length, region), ...] covering the score."""
    length = score.length()
    regions = sorted(score.meters, key=lambda m: m.onset)
    boundaries: list[Fraction] = [Fraction(0)]
    region_starts = {m.onset: m for m in regions}

    pos = Fraction(0)
    region = regions[0]
    while pos < length:
        # a meter change may begin inside the running bar only if it lands on
        # a boundary; callers guarantee that, so at each boundary re-check.
        if pos in region_starts:
            region = region_starts[pos]
        pos += region.bar_length
        boundaries.append(pos)

    grid: list[tuple[Fraction, Fraction, MeterRegion]] = []
    for i, start in enumerate(boundaries[:-1]):
        end = boundaries[i + 1]
        reg = region_starts.get(start)
        if reg is None:
            reg = score.meter_at(start)
        grid.append((start, end - start, reg))
    return grid


# ---------- duration -> notation tokens ----------

# greedy decomposition over binary note values, each optionally single-dotted
_BASES: list[tuple[str, Fraction]] = [
    ("w", Fraction(4)),
    ("h", Fraction(2)),
    ("q", Fraction(1)),
    ("e", Fraction(1, 2)),
    ("16", Fraction(1, 4)),
    ("32", Fraction(1, 8)),
]


def tokenize(rem: Fraction) -> list[tuple[str, int]]:
    """Greedy binary decomposition → [(duration_code, dots), ...]."""
    out: list[tuple[str, int]] = []
    for code, base in _BASES:
        dotted = base + base / 2
        while rem > 0:
            if rem >= dotted:
                out.append((code, 1))
                rem -= dotted
            elif rem >= base:
                out.append((code, 0))
                rem -= base
            else:
                break
    if rem != 0:
        # Non-binary remainder (a tuplet slice cut by a barline). We keep the
        # event musically intact (its onset/duration in the model is exact);
        # the renderer marks such a token as a tuplet slice.
        out.append(("e", 0))
    return out


# ---------- per-voice slicing ----------

def _implicit_rests(items: list[VoiceItem], end: Fraction) -> list[VoiceItem]:
    """Return the timeline with implicit rests filling gaps and the tail.

    Adjacent/gap/explicit rests are merged into one run so a rest spanning a
    barline is one sounded event producing tied rest fragments (and an empty
    measure shows a single whole-measure rest).
    """
    events: list[tuple[Fraction, Fraction, VoiceItem | None]] = []
    # rest runs as (start, end); sounded items are emitted as themselves
    cursor = Fraction(0)

    def extend_rest(run_end: Fraction):
        nonlocal cursor
        if run_end <= cursor:
            return
        if events and events[-1][2] is None and events[-1][1] == cursor:
            s, _, _ = events[-1]
            events[-1] = (s, run_end, None)
        else:
            events.append((cursor, run_end, None))
        cursor = run_end

    for it in sorted(items, key=lambda x: x.onset):
        if it.kind == "rest" and it.tuplet_group_id is None:
            extend_rest(max(cursor, it.end))
            continue
        if it.onset > cursor:
            extend_rest(it.onset)
        events.append((it.onset, it.end, it))
        cursor = max(cursor, it.end)
    if cursor < end:
        extend_rest(end)

    out: list[VoiceItem] = []
    for s, e, it in events:
        if it is None:
            out.append(VoiceItem(
                id=f"rest:{s.numerator}/{s.denominator}", kind="rest",
                onset=s, duration=e - s,
            ))
        else:
            out.append(it)
    return out


def _build_tokens(frag: Fragment, item: VoiceItem, measure_length: Fraction) -> None:
    sounded = item.kind in ("note", "chord")
    is_whole_measure = False

    if item.kind == "rest" and item.tuplet_group_id is None and frag.duration == measure_length:
        # rest filling the whole measure: conventional single whole-rest glyph
        is_whole_measure = True
        codes = [("w", 0)]
    elif frag.tuplet_group_id and frag.head and frag.tail:
        # a complete tuplet member: one tuplet note token
        codes = [("e", 0)]
    else:
        codes = tokenize(frag.duration)

    n = len(codes)
    for i, (code, dots) in enumerate(codes):
        first_in_item = frag.head and i == 0
        last_in_item = frag.tail and i == n - 1
        tok = Token(
            id=f"{item.id}@{frag.onset.numerator}/{frag.onset.denominator}:{i}",
            code=code,
            dots=dots,
            tie_in=sounded and not first_in_item,
            tie_out=sounded and not last_in_item,
            whole_measure=is_whole_measure,
            tuplet_group_id=item.tuplet_group_id,
        )
        frag.tokens.append(tok)


# ---------- entry point ----------

def derive_layout(score: Score) -> dict:
    grid = measure_grid(score)
    measures = []
    voice_fragments: dict[int, dict[str, list[Fragment]]] = {}
    for mi, (start, length, region) in enumerate(grid):
        voice_fragments[mi] = {}
        for v in score.voices:
            voice_fragments[mi][v.id] = _slice_voice_at(v, grid, mi)
        measures.append(MeasureView(
            index=mi, onset=start, duration=length,
            beats=region.beats, beat_unit=region.beat_unit,
        ).to_dict(voice_fragments[mi]))

    return {
        "measures": measures,
        "length": q_json(score.length()),
    }


def _slice_voice_at(voice: Voice, grid, mi: int) -> list[Fragment]:
    start, length, _ = grid[mi]
    end = start + length
    score_end = grid[-1][0] + grid[-1][1]
    timeline = _implicit_rests(voice.items, score_end)
    out: list[Fragment] = []
    for item in timeline:
        if item.end <= start or item.onset >= end:
            continue
        pos = max(item.onset, start)
        slice_end = min(item.end, end)
        frag = Fragment(
            item_id=item.id,
            kind=item.kind,
            onset=pos,
            duration=slice_end - pos,
            local_onset=pos - start,
            head=pos == item.onset,
            tail=slice_end == item.end,
            pitches=list(item.pitches),
            # preserved even when the member is cut by a barline so the
            # renderer can draw a continued 3 bracket on each side
            tuplet_group_id=item.tuplet_group_id,
        )
        _build_tokens(frag, item, length)
        out.append(frag)
    out.sort(key=lambda f: (f.onset, f.item_id))
    return out
