"""Invariants every returned spelling must satisfy, whatever the speller decides.

Parity goldens store line-of-fifths positions, so they cannot see a wrong OCTAVE. Until 0.2.2 the octave was
computed as ``midi // 12 - 1``, which put every B♯ and B𝄪 an octave too high and every C♭ and C𝄫 an octave too
low (B♯ at MIDI 60 came back as B♯4 = MIDI 72). These tests hold the round trip: ``midi_of(pitch) == midi``.
"""
import pytest

from enharmonic import CoreSpeller, PitchClass, Speller, midi_of, pitch_at, pitch_to_string, spell_events
from enharmonic.pitch import LETTER_BASE, Pitch

LETTERS = "CDEFGAB"
ALTERS = (-2, -1, 0, 1, 2)


def _back(p: Pitch) -> int:
    return 12 * (p.octave + 1) + LETTER_BASE[p.step] + p.alter


def test_pitch_at_every_spelling_round_trips():
    n = 0
    for midi in range(128):
        for step in LETTERS:
            for alter in ALTERS:
                if (LETTER_BASE[step] + alter - midi) % 12:
                    continue
                p = pitch_at(PitchClass(step, alter), midi)
                assert _back(p) == midi, f"{pitch_to_string(p)} at {midi}"
                if 0 <= _back(p) <= 127:
                    assert midi_of(p) == midi
                n += 1
    assert n > 256


@pytest.mark.parametrize("step,alter,midi,octave", [
    ("B", 1, 60, 3), ("B", 2, 61, 3), ("C", -1, 71, 5), ("C", -2, 70, 5), ("C", 0, 60, 4), ("B", 0, 71, 4),
])
def test_pitch_at_boundary_by_name(step, alter, midi, octave):
    assert pitch_at(PitchClass(step, alter), midi).octave == octave


# C♯ minor tonic, then its dominant G♯ major with the B♯ leading tone at middle C.
CHORDS = [[49, 56, 61, 64], [44, 56, 60, 63], [49, 56, 61, 64], [44, 56, 60, 63]]


def _events():
    ev, t = [], 0
    for chord in CHORDS:
        ev += [(t, "on", m) for m in chord]
        t += 500
        ev += [(t, "off", m) for m in chord]
    return ev


@pytest.mark.parametrize("make", [Speller, lambda: Speller(look_ahead=True), CoreSpeller])
def test_get_spelling_b_sharp_at_middle_c(make):
    s, t = make(), 0
    for chord in CHORDS:
        for m in chord:
            s.note_on(m, t=t)
        if 60 in chord:
            p = s.get_spelling(60)
            assert p is not None and _back(p) == 60, p
            if p.step == "B":
                assert p.octave == 3
        t += 500
        for m in chord:
            s.note_off(m, t=t)


@pytest.mark.parametrize("mode", ["core", "rt", "la", "tp"])
def test_spell_events_round_trips(mode):
    ev = _events()
    midis = [m for _, typ, m in ev if typ == "on"]
    out = spell_events(ev, mode)
    for p, m in zip(out, midis):
        assert p is None or _back(p) == m, (mode, p, m)
    if mode in ("la", "tp"):  # the real-time modes may read this short excerpt as C
        assert any(p is not None and p.step == "B" and p.alter == 1 for p in out), "B♯ not exercised"
