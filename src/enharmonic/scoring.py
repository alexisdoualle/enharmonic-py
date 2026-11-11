"""Principle 1: interval scoring (port of ``src/scoring.ts``).

    d = 1        P5 / P4                 -> +1     d = 3, 4   3rds / 6ths            -> +1
    d = 0, 2, 5  unison / 2nds / 7ths    ->  0     d = 6..12  augmented / diminished -> -1
    d >= 13      doubly aug / dim        -> -2
"""

from __future__ import annotations

from typing import Sequence

#: Consonance by line-of-fifths distance 0..12 (every larger distance scores -2). Hot paths index this
#: table directly instead of calling :func:`consonance`.
CONSONANCE_BY_DISTANCE: tuple[int, ...] = (0, 1, 0, 1, 1, 0, -1, -1, -1, -1, -1, -1, -1)


def consonance(a: int, b: int) -> int:
    """Consonance of the interval between two line-of-fifths positions, read off their distance."""
    d = abs(a - b)
    if d == 1 or d == 3 or d == 4:
        return 1
    if d == 0 or d == 2 or d == 5:
        return 0
    if d <= 12:
        return -1
    return -2


def interval_score(n: int, scale: Sequence[int]) -> int:
    """Total interval score of candidate ``n`` against the other letters of ``scale`` (by letter slot)."""
    # Integer sum, so the order is free: score all seven slots, then take back the candidate's own letter.
    cons = CONSONANCE_BY_DISTANCE
    total = 0
    for x in scale:
        d = n - x if n >= x else x - n
        total += cons[d] if d < 13 else -2
    x = scale[(n + 1) % 7]
    d = n - x if n >= x else x - n
    return total - (cons[d] if d < 13 else -2)
