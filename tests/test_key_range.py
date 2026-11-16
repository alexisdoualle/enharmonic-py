"""KEY RANGE: a key signature transposes the spelling, at every signature from seven flats to seven sharps.

Proposition: under a notated key signature, a tonal passage is spelled the same way in every key. Transposing the
MIDI and the signature together by k fifths moves every spelling k places along the line of fifths, whatever k: in
C major the dominant's leading tone is F♯, under seven sharps (C♯ major) it is F𝄪, and under seven flats (C♭ major)
the subdominant's fourth is B♭♭. A seven-accidental limit belongs to the signatures a key reader may invent, never
to the keys next to a notated signature, which run to ±8 (G♯ major from C♯ major, F♭ major from C♭ major) and their
secondary dominants further (C𝄪 in C♯ major's V/ii).

Guards the bug class of 0.2.0: the two-pass ``anchored_side_keys`` read a local key only within ±7, so G♯ major (+8)
under seven sharps became A♭ (−4) (fixed in 0.2.1); the look-ahead's ``SpellingEngine.side_key`` had the same ±7
limit (G♯ major under seven sharps re-centred the fold on A♭).

What is asserted, per mode (two-pass with ``key_signatures``, real time and look-ahead with ``key_tonic``/``set_key``):
each passage, spelled in C with no accidental beyond the chromatic notes named below, comes out spelled exactly that
way transposed by k fifths, for k = −7..+7. NOT asserted, by design: keys two or more fifths from the signature
(there the side is the one read from the notes, ``SIGNATURE_NEIGHBOURHOOD``), and keyless runs (the cold start and
the fold basin are parked on a convention, so a keyless passage is not expected to transpose).
"""
import re

import pytest

from enharmonic import Speller
from enharmonic.drive import drive_lof, normalize_events, on_notes
from enharmonic.two_pass import TwoPassOptions, spell_two_pass_lof

SLOT = {"F": 0, "C": 1, "G": 2, "D": 3, "A": 4, "E": 5, "B": 6}
SEMI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
LOF_STEP = "FCGDAEB"


def name_of(n) -> str:
    if n is None:
        return "None"
    a = (n + 1) // 7
    return LOF_STEP[n + 1 - 7 * a] + ("#" * a if a > 0 else "b" * -a)


def note(s: str) -> tuple[int, int]:
    """A spelled note in C: 'F#4' -> (MIDI, line of fifths)."""
    m = re.fullmatch(r"([A-G])(#*|b*)(-?\d)", s)
    assert m
    acc = m.group(2)
    alter = len(acc) if acc.startswith("#") else -len(acc)
    return 12 * (int(m.group(3)) + 1) + SEMI[m.group(1)] + alter, SLOT[m.group(1)] - 1 + 7 * alter


def rep(cs: list[str], k: int) -> list[str]:
    return cs * k


# Chords spelled in C major / A minor, bass first.
C, F, G7, G, Am, Dm = "C3 E4 G4 C5", "F3 F4 A4 C5", "G2 F4 B4 D5", "G2 D4 G4 B4", "A2 E4 A4 C5", "D3 D4 F4 A4"
D7, A7, E7, C7, B7 = "D3 F#4 A4 C5", "A2 C#4 E4 G4", "E3 D4 G#4 B4", "C3 E4 G4 Bb4", "B2 D#4 F#4 A4"
IN_G = ["G2 D4 G4 B4", "C3 E4 G4 C5", "D3 C4 F#4 A4", "G2 D4 G4 B4"]      # I–IV–V7–I in G
IN_F = ["F2 C4 F4 A4", "Bb2 D4 F4 Bb4", "C3 Bb3 E4 G4", "F2 C4 F4 A4"]    # I–IV–V7–I in F
CAD = [C, F, G7, C]

PASSAGES = [
    ("I–IV–V7–I cadences", rep(CAD, 16), 0),
    # The dominant as a key: G major with F♯ in C; G♯ major with F𝄪 under seven sharps (+8 on the line of fifths).
    ("modulation to the dominant and back", [*rep(CAD, 8), C, Am, D7, G, *rep(IN_G, 30), G7, C, *rep(CAD, 8)], 0),
    # The subdominant as a key: F major with B♭ in C; F♭ major with B♭♭ under seven flats (−8).
    ("modulation to the subdominant and back", [*rep(CAD, 8), C, C7, F, *rep(IN_F, 30), G7, C, *rep(CAD, 8)], 0),
    # V/ii, V/vi, V/V as chords: C♯, G♯, F♯ in C; B♯, F𝄪, E♯ … under seven sharps.
    ("secondary dominants", rep([C, A7, Dm, E7, Am, D7, G, G7, C, F, G7, C], 8), 0),
    # A minor with V/iv, V/V and V: under seven sharps (A♯ minor) B♯–D𝄪–F𝄪 and E♯–G𝄪–B♯.
    ("minor key with secondary dominants",
     [*rep([Am, Dm, E7, Am], 2), *rep([Am, Dm, B7, E7, Am, A7, Dm, E7, Am], 8)], 0),
    # The composer writes the dominant's signature for the G-major section (one sharp more), then the tonic's again.
    ("modulation to the dominant under its own signature",
     [*rep(CAD, 8), C, Am, D7, G, *rep(IN_G, 30), G7, C, *rep(CAD, 8)], 1),
]
# The section of the re-signed passage under the dominant's signature (chord indices).
RESIGN_FROM, RESIGN_TO = 36, 36 + 120


def shift_for(k: int) -> int:
    """MIDI shift (−5..+6 semitones) that takes C to the major tonic k fifths away."""
    s = (7 * k) % 12
    return s - 12 if s > 6 else s


def build(chords: list[str], resign: int, k: int):
    """The passage transposed to signature k: events, expected line of fifths per note, signature per note/chord."""
    ev, want, sigs, sig_at_on = [], [], [], []
    t = 0
    for ci, ch in enumerate(chords):
        sig = k + (resign if resign and RESIGN_FROM <= ci < RESIGN_TO else 0)
        ns = sorted(((m + shift_for(k), n + k) for m, n in map(note, ch.split())), key=lambda x: x[0])
        for m, n in ns:
            ev.append((t, "on", m))
            want.append(n)
            sigs.append(sig)
        sig_at_on.append(sig)
        t += 500
        for m, _ in ns:
            ev.append((t, "off", m))
    return ev, want, sigs, sig_at_on


class _KeyedProxy:
    """Streaming: key_tonic at the start, set_key before the first note of each chord whose signature changed."""

    def __init__(self, s: Speller, sig_at_on: list[int]):
        self.s, self.sig_at_on = s, sig_at_on
        self.look_ahead = s.look_ahead
        self.chord, self.last_t, self.cur = -1, None, sig_at_on[0]

    def note_on(self, midi, t=None, *args, **kw):
        if t != self.last_t:
            self.chord += 1
            self.last_t = t
            sig = self.sig_at_on[self.chord]
            if sig != self.cur:
                self.s.set_key(sig)
                self.cur = sig
        self.s.note_on(midi, t, *args, **kw)

    def note_off(self, midi, t=None):
        self.s.note_off(midi, t)

    def get_lof(self, midi):
        return self.s.get_lof(midi)


def spell(mode: str, b) -> list:
    ev, _, sigs, sig_at_on = b
    if mode == "two-pass":
        return spell_two_pass_lof(on_notes(ev), TwoPassOptions(key_signatures=sigs))
    s = Speller(look_ahead=mode == "look-ahead", key_tonic=sig_at_on[0])
    return drive_lof(_KeyedProxy(s, sig_at_on), normalize_events(ev))


def first_miss(mode: str, chords, resign: int, k: int) -> str:
    b = build(chords, resign, k)
    got, want = spell(mode, b), b[1]
    bad = [i for i in range(len(got)) if got[i] != want[i]]
    if not bad:
        return ""
    i = bad[0]
    return f"signature {k}: {len(bad)} notes off, first #{i} {name_of(got[i])} (want {name_of(want[i])})"


@pytest.mark.parametrize("mode", ["two-pass", "real-time", "look-ahead"])
@pytest.mark.parametrize("name,chords,resign", PASSAGES, ids=[p[0] for p in PASSAGES])
def test_transposes_with_the_signature(mode, name, chords, resign):
    misses = []
    for k in range(-7, 8):
        if resign and k + resign > 7:   # no signature past seven sharps
            continue
        m = first_miss(mode, chords, resign, k)
        if m:
            misses.append(m)
    assert misses == []
