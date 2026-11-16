"""Pitch and PitchClass: plain immutable values (port of ``src/pitch.ts``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

Letter = Literal["C", "D", "E", "F", "G", "A", "B"]
#: Double-flat through double-sharp. The Speller does not produce more remote spellings.
Accidental = Literal[-2, -1, 0, 1, 2]


@dataclass(frozen=True)
class PitchClass:
    step: str
    alter: int


@dataclass(frozen=True)
class Pitch:
    step: str
    alter: int
    #: Scientific Pitch Notation octave number. Middle C = C4.
    octave: int

    @property
    def pitch_class(self) -> PitchClass:
        return PitchClass(self.step, self.alter)


#: Natural pitch class of each letter (C = 0), for the {step, alter} API.
LETTER_BASE: dict[str, int] = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

_ACCIDENTAL_SUFFIX: dict[int, str] = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}


def midi_of(p: Pitch) -> int:
    """Standard MIDI number for a Pitch. C4 = 60. Raises ValueError outside 0..127."""
    midi = 12 * (p.octave + 1) + LETTER_BASE[p.step] + p.alter
    if midi < 0 or midi > 127:
        raise ValueError(f"midi_of: {pitch_to_string(p)} (midi {midi}) out of range 0..127")
    return midi


def pitch_at(pc: Union[PitchClass, Pitch], midi: int) -> Pitch:
    """The Pitch that spells ``midi`` as ``pc``. The written octave follows the LETTER, not the MIDI number:
    B♯3 and C4 are both MIDI 60, C♭5 and B4 are both MIDI 71 (B𝄪3 = 61, C𝄫5 = 70). Never compute the octave
    as ``midi // 12 - 1``, which is wrong whenever the accidental crosses the B–C boundary."""
    return Pitch(pc.step, pc.alter, (midi - pc.alter - LETTER_BASE[pc.step]) // 12 - 1)


def pitch_class_value(p: Union[PitchClass, Pitch]) -> int:
    """12-tone pitch class (0..11). C = 0."""
    return (LETTER_BASE[p.step] + p.alter) % 12


def pitch_to_string(p: Union[Pitch, PitchClass]) -> str:
    """Human-readable string. "C#4" for Pitch, "Eb" for PitchClass."""
    base = f"{p.step}{_ACCIDENTAL_SUFFIX[p.alter]}"
    return f"{base}{p.octave}" if isinstance(p, Pitch) else base
