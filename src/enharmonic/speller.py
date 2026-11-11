"""Speller: the shipped real-time product API (port of ``src/speller.ts``).

    Speller()                 -> real-time
    Speller(look_ahead=True)  -> near-real-time (the caller feeds resolve_dir / resolve_exact)

For offline use with the whole piece in hand, see :func:`enharmonic.spell_two_pass`.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .engine import LA_PRESET, RT_PRESET, SpellingEngine, collection_at, with_onset_tolerance
from .pitch import Pitch, PitchClass


class Speller:
    def __init__(self, look_ahead: bool = False, key_tonic: Optional[int] = None, onset_tolerance: float = 0) -> None:
        """
        :param look_ahead: letter-aware look-ahead; pass ``resolve_dir`` / ``resolve_exact`` to
            :meth:`note_on` from a small forward buffer (see :func:`enharmonic.drive`).
        :param key_tonic: PROTOTYPE key hint, the signed line-of-fifths of the MAJOR tonic (C=0, G=1, F=-1,
            Db=-5; minor: its relative major). Re-centres the fold basin and seeds the opening frame.
        :param onset_tolerance: group notes that start within this many ``t`` units of an onset's first note
            into that onset, unless the pitch repeats. Default 0 (exact ``t`` grouping).
        """
        self.look_ahead = look_ahead
        preset = LA_PRESET if look_ahead else RT_PRESET
        self._engine = SpellingEngine(with_onset_tolerance(preset, onset_tolerance) if onset_tolerance else preset)
        if key_tonic is not None:
            self._engine.set_key(key_tonic)
            self._engine.reset(collection_at(key_tonic + 2))

    def set_key(self, key_tonic: int) -> None:
        """Re-centre the spiral fold basin on a new key mid-stream, without clearing the running frame."""
        self._engine.set_key(key_tonic)

    def note_on(self, midi: int, t: Optional[float] = None, resolve_dir: int = 0, resolve_exact: int = 0) -> None:
        self._engine.note_on(midi, t, resolve_dir, resolve_exact)

    def note_off(self, midi: int, t: Optional[float] = None) -> None:
        self._engine.note_off(midi, t)

    def get_spelling(self, midi: int) -> Optional[Pitch]:
        return self._engine.get_spelling(midi)

    def get_lof(self, midi: int) -> Optional[int]:
        """The line-of-fifths position of :meth:`get_spelling` (C=0, G=1, F=-1, ...), or None."""
        return self._engine.get_lof(midi)

    def reset(self, scale: Optional[Iterable[PitchClass]] = None, hard: bool = False) -> None:
        """Re-seed. No argument clears frame state (cold start); a 7-letter scale is a soft key hint."""
        self._engine.reset(scale if scale is not None else (), hard)

    def get_resolved_scale(self) -> list[PitchClass]:
        """Current 7-letter surface."""
        return self._engine.get_resolved_scale()

    @property
    def engine(self) -> SpellingEngine:
        """The underlying engine (introspection: ``speller.engine.decision()``)."""
        return self._engine
