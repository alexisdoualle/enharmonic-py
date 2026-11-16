"""CoreSpeller: four principles, nothing else (port of ``src/core.ts``).

Interval scoring, the 7-letter limit, a recency guard and a spiral fold. One scale, its slots overwritten
and never reverted. Not part of the public ``Speller`` API in TypeScript either (the bench scores it as
Core); exported here for parity and experiments.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

from ._js import js_sign
from .lof import C_MAJOR, LETTER_ORDER, acc, candidates, letter, lof_of, pitch_class, spelling_of
from .pitch import Pitch, PitchClass, pitch_at
from .scoring import CONSONANCE_BY_DISTANCE


class CoreSpeller:
    def __init__(
        self,
        recency_guard: int = 2,
        guard_window: int = 3,
        double_accidental_penalty: int = 0,
        fold_radius: int = 8,
    ) -> None:
        self._recency_guard = recency_guard
        self._guard_window = guard_window
        self._double_accidental_penalty = double_accidental_penalty
        self._fold_radius = fold_radius
        # Principle 2: one spelling (line-of-fifths position) per letter slot.
        self._scale: list[int] = list(C_MAJOR)
        # midi -> the spelling committed for that sounding note.
        self._active: dict[int, int] = {}
        # Note-ons per pitch still ringing.
        self._held: dict[int, int] = {}
        # Principle 3 memory: letter slot -> (onset, n) last committed there.
        self._last_by_letter: list[Optional[tuple[int, int]]] = [None] * 7
        self._onset = -1
        self._last_t: float = math.nan

    # TypeScript has no look-ahead on Core; `lookAhead` is undefined (falsy) for the batch driver.
    look_ahead = False

    def reset(self, scale: Iterable[PitchClass]) -> None:
        self._scale = list(C_MAJOR)
        self._last_by_letter = [None] * 7
        # `active` is kept: a held note keeps its spelling across a reset.
        for pc in scale:
            n = lof_of(pc)
            self._scale[letter(n)] = n

    def note_on(self, midi: int, t: Optional[float] = None, resolve_dir: int = 0, resolve_exact: int = 0) -> None:
        """Commit a spelling for ``midi``. ``resolve_dir``/``resolve_exact`` are accepted and ignored."""
        # TS: if (t === undefined || t !== this.lastT) { if (t !== undefined) this.lastT = t; this.onset++; }
        if t is None or t != self._last_t:
            if t is not None:
                self._last_t = t
            self._onset += 1
        best: Optional[int] = None
        best_score = -math.inf
        scale = self._scale
        cons = CONSONANCE_BY_DISTANCE
        dap = self._double_accidental_penalty
        rg = self._recency_guard
        gw = self._guard_window
        onset = self._onset
        last_by_letter = self._last_by_letter
        for n in candidates(midi):
            ln_ = (n + 1) % 7  # letter(n)
            # interval_score(n, scale), inlined (integer sum, order-free)
            s = 0
            for x in scale:
                d = n - x if n >= x else x - n
                s += cons[d] if d < 13 else -2
            x = scale[ln_]
            d = n - x if n >= x else x - n
            s -= cons[d] if d < 13 else -2
            if dap and abs((n + 1) // 7) >= 2:
                s -= dap
            last = last_by_letter[ln_]
            if rg and last is not None and last[1] != n and onset - last[0] <= gw:
                s -= rg
            if s > best_score:
                best_score = s
                best = n
        if best is None:
            return
        self._scale[letter(best)] = best
        self._active[midi] = best
        self._held[midi] = self._held.get(midi, 0) + 1
        self._last_by_letter[letter(best)] = (self._onset, best)
        # Principle 4: spiral fold around D.
        off = sum(self._scale) - 7 * 2
        if self._fold_radius and abs(off) > 7 * self._fold_radius:
            k = -12 * js_sign(off)
            for n in list(self._scale):
                self._scale[letter(n + k)] = n + k

    def note_off(self, midi: int, t: Optional[float] = None) -> None:
        c = self._held.get(midi, 1) - 1
        if c > 0:
            self._held[midi] = c
        else:
            self._held.pop(midi, None)
            self._active.pop(midi, None)

    def get_resolved_scale(self) -> list[PitchClass]:
        """The current scale, C to B."""
        return [spelling_of(self._scale[s]) for s in LETTER_ORDER]

    def get_lof(self, midi: int) -> Optional[int]:
        """Line-of-fifths position of :meth:`get_spelling` (parity helper)."""
        n = self._active.get(midi)
        if n is None:
            pc = midi % 12
            n = next((m for m in (self._scale[s] for s in LETTER_ORDER) if pitch_class(m) == pc), None)
        return n

    def get_spelling(self, midi: int) -> Optional[Pitch]:
        """The spelling of a sounding note (its committed one), else the scale's spelling of its pitch class."""
        n = self.get_lof(midi)
        if n is None:
            return None
        sp = spelling_of(n)
        return pitch_at(sp, midi)
