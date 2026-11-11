"""The line of fifths: the one integer every spelling is inside the speller (port of ``src/lof.ts``).

    ... A♭  E♭  B♭  F   C   G   D   A   E   B   F♯  C♯  G♯  D♯ ...
    ... -4  -3  -2  -1  0   1   2   3   4   5   6   7   8   9  ...

+1 is a fifth, +7 the same letter one accidental sharper, +12 the enharmonic comma. A scale is indexed by
LETTER SLOT, the letter's place in fifths order (F=0, C=1, G=2, D=3, A=4, E=5, B=6).
"""

from __future__ import annotations

from typing import Mapping

from ._js import js_round
from .pitch import PitchClass

#: The seven letters in fifths order, indexed by letter slot.
FIFTHS: tuple[str, ...] = ("F", "C", "G", "D", "A", "E", "B")

#: Letter slots in note-name order C D E F G A B.
LETTER_ORDER: tuple[int, ...] = (1, 3, 5, 0, 2, 4, 6)

#: C major, indexed by letter slot: the cold-start scale.
C_MAJOR: tuple[int, ...] = (-1, 0, 1, 2, 3, 4, 5)

#: The enharmonic comma.
COMMA = 12

_SLOT: dict[str, int] = {"F": 0, "C": 1, "G": 2, "D": 3, "A": 4, "E": 5, "B": 6}


def letter(n: int) -> int:
    """Letter slot of n, 0..6 in fifths order. (TS: ((n + 1) % 7 + 7) % 7.)"""
    return (n + 1) % 7


def acc(n: int) -> int:
    """Accidental of n: -1 flat, 0 natural, +1 sharp. (TS: Math.floor((n + 1) / 7).)"""
    return (n + 1) // 7


def pitch_class(n: int) -> int:
    """Pitch class of n (0-11). (TS: ((7 * n) % 12 + 12) % 12.)"""
    return (7 * n) % 12


def lof_of(p: "PitchClass | Mapping[str, int]") -> int:
    """n of a {step, alter} spelling."""
    if isinstance(p, Mapping):
        return _SLOT[p["step"]] - 1 + 7 * p["alter"]  # type: ignore[index]
    return _SLOT[p.step] - 1 + 7 * p.alter


def spelling_of(n: int) -> PitchClass:
    """{step, alter} of n."""
    return PitchClass(FIFTHS[letter(n)], acc(n))


def letter_at(s: int, c: float) -> int:
    """n of letter slot ``s`` with the accidental that lands it nearest centre ``c``, clamped to [-2, +2].

    Uses JavaScript ``Math.round`` (ties toward +infinity); ``c`` may be a non-integer mean.
    """
    alter = max(-2, min(2, js_round((c - (s - 1)) / 7)))
    return s - 1 + 7 * alter


def _build_candidates() -> tuple[tuple[int, ...], ...]:
    by_pc: list[list[int]] = [[] for _ in range(12)]
    for n in range(-15, 20):
        by_pc[pitch_class(n)].append(n)
    # TS comparator: |acc(a)| - |acc(b)| || acc(b) - acc(a); stable sort.
    return tuple(tuple(sorted(pile, key=lambda x: (abs(acc(x)), -acc(x)))) for pile in by_pc)


#: The 35 spellings F♭♭ (-15) to B♯♯ (+19), bucketed by pitch class, plainest first (sharp before flat on a tie).
_CANDIDATES = _build_candidates()


def candidates(midi: int) -> tuple[int, ...]:
    """Every spelling of ``midi``'s pitch class within a double accidental, plainest first."""
    return _CANDIDATES[midi % 12]


__all__ = [
    "FIFTHS", "LETTER_ORDER", "C_MAJOR", "COMMA", "letter", "acc", "pitch_class", "lof_of",
    "spelling_of", "letter_at", "candidates",
]
