"""The held-letter target (mirrors prod test/speller.test.ts 'held-letter target')."""

from dataclasses import replace

from enharmonic import LocalKey
from enharmonic.engine import LA_PRESET, SpellingEngine
from enharmonic.key import is_wide_leading_tone


def _tok(p):
    return p.step + ("#" * p.alter if p.alter > 0 else "b" * -p.alter)


def _votes(chords, key, held=True):
    """The look-ahead votes on F#/Gb rising (resolve_dir +1) after ``chords``, with the leading-tone gate's key."""
    e = SpellingEngine(replace(LA_PRESET, held_letter_target=held))
    for k, c in enumerate(chords):
        if k:
            for m in chords[k - 1]:
                e.note_off(m, k * 500)
        for m in c:
            e.note_on(m, k * 500)
    e.note_on(66, len(chords) * 500, 1, 1, None, 0, key)
    return " ".join(sorted(f"{_tok(c.c)}{c.la_delta}" for c in e.decision().candidates))


# C major, then Eb-Gb-Bb-Db over it: the G slot holds Gb, so F# rising to G finds no G in the surface.
C_M = [48, 60, 64, 67]
CTX = [C_M, C_M, [51, 54, 58, 61]]


def test_f_sharp_rising_to_g_while_g_flat_holds_the_slot():
    assert _votes(CTX, LocalKey(7, False)) == "E##0 F#2 Gb-2"  # G major: F# is its natural 7
    assert _votes(CTX, LocalKey(7, False), held=False) == "E##0 F#0 Gb0"
    assert _votes(CTX, LocalKey(1, False)) == "E##0 F#0 Gb0"  # Db major: Gb is its 4th
    assert _votes(CTX, None) == "E##0 F#0 Gb0"


def test_is_wide_leading_tone():
    c, am = LocalKey(0, False), LocalKey(9, True)
    assert [p for p in range(12) if is_wide_leading_tone(p, c)] == [1, 2, 6, 8, 11]
    assert [p for p in range(12) if is_wide_leading_tone(p, am)] == [3, 6, 8, 10, 11]
