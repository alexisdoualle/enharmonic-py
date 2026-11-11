"""Enharmonic candidate generation (port of ``src/candidates.ts``).

For a MIDI value, every letter whose pitch class matches it with an accidental in [-2, +2], ordered by
|alter| ascending, then sharp before flat:

    enharmonic_candidates_for(0) -> [C, B#, Dbb]
    enharmonic_candidates_for(1) -> [C#, Db, B##]
    enharmonic_candidates_for(8) -> [G#, Ab]
"""

from __future__ import annotations

from .lof import candidates, spelling_of
from .pitch import PitchClass


def enharmonic_candidates_for(midi: int) -> list[PitchClass]:
    return [spelling_of(n) for n in candidates(midi)]
