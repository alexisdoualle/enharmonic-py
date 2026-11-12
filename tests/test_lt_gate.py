"""The leading-tone gate and local keys (mirrors prod test/speller.test.ts 'leading-tone gate')."""

from dataclasses import replace

from enharmonic import LocalKey, TwoPassNote, TwoPassOptions, local_keys, spell_two_pass
from enharmonic.engine import LA_PRESET, SpellingEngine

D_MINOR = [[50, 62, 65, 69], [55, 62, 67, 70], [57, 61, 64, 67], [50, 62, 65, 69]]
DM = LocalKey(2, True)


def _tok(p):
    return p.step + ("#" * p.alter if p.alter > 0 else "b" * -p.alter)


def _notes(chords, dt=500):
    return [TwoPassNote(m, k * dt, k * dt + dt) for k, c in enumerate(chords) for m in c]


def _votes(chords, midi, resolve_dir, key, gate=True):
    """The look-ahead votes (la_delta) on ``midi`` struck after ``chords``."""
    e = SpellingEngine(replace(LA_PRESET, leading_tone_gate=gate))
    for k, c in enumerate(chords):
        if k:
            for m in chords[k - 1]:
                e.note_off(m, k * 500)
        for m in c:
            e.note_on(m, k * 500)
    e.note_on(midi, len(chords) * 500, resolve_dir, 0, None, 0, key)
    return " ".join(sorted(f"{_tok(c.c)}{c.la_delta}" for c in e.decision().candidates))


def test_local_keys_reads_d_minor_with_either_table():
    for table in ("temperley", "tonic-triad"):
        assert all(k == DM for k in local_keys(_notes(D_MINOR), table, 6)), table


def test_local_keys_window_is_centred():
    c_maj, fs_maj = [60, 64, 67], [66, 70, 73]
    k = local_keys(_notes([c_maj] * 4 + [fs_maj] * 4), radius=1)
    assert k[0].tonic == 0 and k[-1].tonic == 6


def test_falling_leading_tone_gets_no_letter_step_vote():
    with_c = D_MINOR + [[48, 60, 64, 67]]
    assert _votes(with_c, 61, -1, DM) == "B##0 C#0 Db0"
    assert _votes(with_c, 61, -1, None) == "B##0 C#-2 Db2"
    assert _votes(with_c, 61, -1, DM, gate=False) == "B##0 C#-2 Db2"


def test_rising_notes_and_non_leading_tones_keep_the_look_ahead():
    assert _votes(D_MINOR, 61, 1, DM) == "B##0 C#3 Db-3"
    assert "2" in _votes(D_MINOR, 63, -1, DM)


def test_two_pass_gate_can_be_switched_off():
    notes = _notes(D_MINOR + [[53, 61], [53, 60], [58, 62, 65]])
    assert len(spell_two_pass(notes, TwoPassOptions(leading_tone_gate=False))) == len(notes)
    assert len(spell_two_pass(notes)) == len(notes)


def test_local_keys_fast_path_matches_exact():
    """local_keys ranks keys from float prefix sums and defers near-ties to the exact TypeScript-order
    computation; it must name the same key as local_keys_exact everywhere, flat and sparse windows included."""
    from harness import random_inputs

    from enharmonic.drive import on_notes
    from enharmonic.key import local_keys_exact

    streams = [on_notes(ev) for ev in random_inputs().values()]
    streams += [_notes([[60]] * 3), _notes([list(range(60, 72))] * 3), _notes([[60, 66]] * 5), []]
    for notes in streams:
        for table, radius in (("temperley", 6), ("tonic-triad", 12), ("temperley", 0)):
            assert local_keys(notes, table, radius) == local_keys_exact(notes, table, radius)
