"""Diatonic intervals: plain immutable values (port of ``src/interval.ts``).

The chroma-based interval-quality formula in :func:`_quality_from_chroma` is adapted from Brian Bowman's
meantonal project (https://github.com/meantonal/meantonal-js, MIT License), as in the TypeScript library.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .lof import lof_of
from .pitch import PitchClass

#: Signed quality -> label. 0=P, ±1=M/m, ±2=A/d, ±3=AA/dd.
_QUALITY_LABEL: dict[int, str] = {0: "P", 1: "M", -1: "m", 2: "A", -2: "d", 3: "AA", -3: "dd"}


@dataclass(frozen=True)
class Interval:
    #: 'P', 'M', 'm', 'A', 'd', 'AA' or 'dd'.
    quality: str
    #: 1 (unison) through 8 (octave). Always reduced.
    number: int


def _quality_from_chroma(c: int) -> int:
    """Signed interval quality as a pure function of line-of-fifths chroma."""
    if abs(c) <= 1:
        return 0
    if 0 < c <= 5:
        return math.floor((c + 5) / 7)
    if -5 <= c < 0:
        return math.ceil((c - 5) / 7)
    if c > 5:
        return math.floor((c + 8) / 7)
    return math.floor((c - 2) / 7)


def raw_interval_between(frm: PitchClass, to: PitchClass) -> tuple[int, int]:
    """Internal: raw ``(quality, number)``, NOT clamped (quality may be ±4 or beyond)."""
    interval_chroma = lof_of(to) - lof_of(frm)
    letter_dist = (4 * interval_chroma) % 7  # TS: ((4 * c) % 7 + 7) % 7
    stepspan = 7 if letter_dist == 0 and interval_chroma < 0 else letter_dist
    return _quality_from_chroma(interval_chroma), stepspan + 1


def interval_between(frm: PitchClass, to: PitchClass) -> Interval:
    """Interval from ``frm`` up to ``to``, reduced to 1..8; qualities beyond AA/dd are clamped."""
    raw_q, number = raw_interval_between(frm, to)
    clamped = max(-3, min(3, raw_q))
    return Interval(_QUALITY_LABEL[clamped], number)


def interval_label(i: Interval) -> str:
    """Standard label, e.g. "P5", "m3", "AA4"."""
    return f"{i.quality}{i.number}"
