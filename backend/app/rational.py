"""Exact rational time arithmetic (quarter-length units).

All musical time in score-rebar-web is expressed in quarter notes and stored
as fractions so that triplets (e.g. an eighth inside a 3:2 group = 2/3 QL)
never become 0.66667 and drift across barlines.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any


def as_q(value: Any) -> Fraction:
    """Coerce int/float/{'num','den'}/'a/b' string to Fraction QL."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, dict):
        return Fraction(int(value["num"]), int(value["den"]))
    if isinstance(value, float):
        return Fraction(value).limit_denominator(10_000)
    if isinstance(value, str):
        if "/" in value:
            a, b = value.split("/", 1)
            return Fraction(int(a), int(b))
        return Fraction(int(value), 1)
    return Fraction(int(value), 1)


def q_json(value: Fraction) -> dict:
    return {"num": value.numerator, "den": value.denominator}
