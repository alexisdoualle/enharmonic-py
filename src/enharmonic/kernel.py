"""Look-ahead resolution-step helpers (port of ``src/kernel.ts``).

TypeScript's ``NoteContext`` ({resolveDir, resolveExact, t}) becomes keyword arguments on
:meth:`enharmonic.Speller.note_on`.
"""

from __future__ import annotations


def resolve_step(frm: int, to: int) -> int:
    """Semitone step from ``frm`` to ``to`` IGNORING the octave: +1 up, -1 down, 0 otherwise."""
    iv = (to - frm) % 12  # TS: (((to - from) % 12) + 12) % 12
    return 1 if iv == 1 else -1 if iv == 11 else 0


def resolve_exact_step(frm: int, to: int) -> int:
    """Semitone step from ``frm`` to ``to`` at the exact pitch: +1, -1, or 0."""
    return 1 if to == frm + 1 else -1 if to == frm - 1 else 0
