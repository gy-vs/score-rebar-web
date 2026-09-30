"""Musical edits applied to voice timelines.

Every operation works on absolute quarter-note time within one voice — never
on measures or fragment ids.  An edit is either *accepted* (model + version
advance atomically) or *rejected* with a structured reason; on rejection the
model is untouched and the caller keeps the user's draft.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .models import MeterRegion, Pitch, Score, TupletGroup, Voice, VoiceItem, new_id
from .rational import as_q


class EditError(Exception):
    def __init__(self, code: str, message: str, conflict=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.conflict = conflict


# Durations the editor can assign directly (QL). Tuplet member durations are
# owned by the group and cannot be set per member.
ALLOWED_DURATIONS: list[Fraction] = [
    Fraction(4), Fraction(3), Fraction(2), Fraction(3, 2),
    Fraction(1), Fraction(3, 4), Fraction(1, 2), Fraction(3, 8),
    Fraction(1, 4), Fraction(1, 8),
]
# snapping grid for moves: 16th-note (1/4 QL)
GRID = Fraction(1, 4)


@dataclass
class Occupancy:
    item_id: str
    start: Fraction
    end: Fraction


def _occupancy(voice: Voice, ignore_id: str | None = None):
    """Sounded time claimed by non-rest items (rests are free space)."""
    out = []
    for it in voice.items:
        if it.id == ignore_id or it.kind == "rest":
            continue
        out.append(Occupancy(it.id, it.onset, it.end))
    return out


def _check_free(voice: Voice, start: Fraction, end: Fraction, ignore_id: str | None) -> None:
    for o in _occupancy(voice, ignore_id):
        if start < o.end and o.start < end:
            raise EditError(
                "overlap",
                f"{start}–{end} QL 与另一已发声事件 "
                f"{o.item_id}（{o.start}–{o.end}）重叠",
                {"itemId": o.item_id,
                 "start": {"num": o.start.numerator, "den": o.start.denominator},
                 "end": {"num": o.end.numerator, "den": o.end.denominator}},
            )


def _find_item(score: Score, item_id: str) -> tuple[Voice, VoiceItem]:
    found = score.get_item(item_id)
    if found is None:
        raise EditError("not_found", f"事件 {item_id} 不存在")
    return found


def _tuplet_lock(score: Score, item: VoiceItem) -> TupletGroup:
    g = score.tuplet_group_of(item)
    if g is not None:
        raise EditError(
            "tuplet_locked",
            "该事件属于连音组；连音组的成员作为一个整体锁定，"
            "不能单独改时值或移动（以保持实际发声时值）。",
            {"tupletGroupId": g.id, "memberIds": list(g.member_ids)},
        )
    return g  # unreachable but keeps type checkers calm


# ---------- operations ----------

def set_meter(score: Score, from_measure: int, beats: int, beat_unit: int) -> None:
    """Set the meter at the start of ``from_measure`` (0-based).

    Only recomputes meter regions; voice timelines are not touched.
    """
    from .layout import measure_grid
    if beats < 1 or beats > 16 or beat_unit not in (2, 4, 8):
        raise EditError("bad_meter", f"不支持的拍号 {beats}/{beat_unit}")
    grid = measure_grid(score)
    if not (0 <= from_measure < len(grid)):
        raise EditError("bad_measure", f"小节 {from_measure} 超出范围（共 {len(grid)} 小节）")
    onset = grid[from_measure][0]
    # replace region starting at same onset, else insert; drop later identical
    kept = [m for m in score.meters if m.onset != onset]
    kept.append(MeterRegion(onset=onset, beats=beats, beat_unit=beat_unit))
    score.meters = sorted(kept, key=lambda m: m.onset)
    # remove regions made redundant by a preceding identical meter is *not*
    # done: explicit meter events are preserved, like a real score engraver.
    score.bump()


def change_pitch(score: Score, item_id: str, pitches: list[dict]) -> None:
    voice, item = _find_item(score, item_id)
    if item.kind == "rest":
        # rest -> note/chord: it stays a sounded event at the same time
        item.kind = "chord" if len(pitches) > 1 else "note"
    else:
        item.kind = "chord" if len(pitches) > 1 else "note"
    item.pitches = [Pitch.from_dict(p) for p in pitches]
    score.bump()


def change_duration(score: Score, item_id: str, duration: Fraction,
                    allow_extend: bool = True) -> None:
    voice, item = _find_item(score, item_id)
    _tuplet_lock(score, item)
    dur = as_q(duration)
    if dur <= 0:
        raise EditError("bad_duration", "时值必须为正")
    if dur not in ALLOWED_DURATIONS:
        raise EditError("bad_duration",
                        f"不支持的时值 {dur} QL（支持全/二/四/八/十六分及附点）")
    if item.kind == "rest":
        item.duration = dur
        score.bump()
        return
    _check_free(voice, item.onset, item.onset + dur, item.id)
    item.duration = dur
    score.bump()


def move_item(score: Score, item_id: str, new_onset: Fraction) -> None:
    """Move a sounded event (or rest) to another time within the same voice."""
    voice, item = _find_item(score, item_id)
    _tuplet_lock(score, item)
    target = as_q(new_onset)
    if target < 0:
        raise EditError("bad_onset", "不能移动到乐谱开头之前")
    # snap to the 16th-note grid
    steps = target / GRID
    snapped_steps = (steps.numerator + steps.denominator // 2) // steps.denominator
    target = Fraction(snapped_steps) * GRID
    if item.kind != "rest":
        _check_free(voice, target, target + item.duration, item.id)
    item.onset = target
    score.bump()


def insert_rest(score: Score, voice_id: str, onset: Fraction, duration: Fraction) -> VoiceItem:
    voice = score.get_voice(voice_id)
    if voice is None:
        raise EditError("not_found", f"声部 {voice_id} 不存在")
    onset, duration = as_q(onset), as_q(duration)
    if duration <= 0:
        raise EditError("bad_duration", "时值必须为正")
    rest = VoiceItem(id=new_id("ev"), kind="rest", onset=onset, duration=duration)
    voice.items.append(rest)
    score.bump()
    return rest


# ---------- proposed-edit persistence shape ----------

def proposal_response(prop: dict) -> dict:
    """A rejected edit stays on the server as a *pending proposal*.

    It carries the user's intended change verbatim plus its relation to the
    currently confirmed timeline, so reopening the document still shows it.
    """
    return {
        "id": prop["id"],
        "op": prop["op"],
        "args": prop["args"],
        "status": "pending",
        "reason": prop.get("reason"),
        "baseVersion": prop.get("base_version"),
    }
