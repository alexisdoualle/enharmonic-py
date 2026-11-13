"""The key-degree gate (mirrors prod test/speller.test.ts 'key-degree gate')."""

from dataclasses import replace

from enharmonic import LocalKey, TwoPassNote, TwoPassOptions, degree_keys, spell_two_pass
from enharmonic.engine import LA_PRESET, SpellingEngine
from enharmonic.key import is_key_degree, local_keys, local_keys_exact


def _tok(p):
    return p.step + ("#" * p.alter if p.alter > 0 else "b" * -p.alter)


def _votes(chords, midi, resolve_dir, key, gate=True):
    """The look-ahead votes on the last note after ``chords``, struck with ``resolve_dir`` and the key-degree key."""
    e = SpellingEngine(replace(LA_PRESET, key_degree_gate=gate))
    for k, c in enumerate(chords):
        if k:
            for m in chords[k - 1]:
                e.note_off(m, k * 500)
        for m in c:
            e.note_on(m, k * 500)
    e.note_on(midi, len(chords) * 500, resolve_dir, 0, None, 0, None, key)
    return " ".join(sorted(f"{_tok(c.c)}{c.la_delta}" for c in e.decision().candidates))


# C major, then D major: the surface spells F#, so F rising to F# has a letter-step target.
CTX = [[48, 60, 64, 67], [50, 62, 66, 69]]


def test_f_rising_to_f_sharp_keeps_f_in_c_major_and_votes_e_sharp_in_a_major():
    assert _votes(CTX, 65, 1, LocalKey(0, False)) == "E#0 F0 Gbb0"
    assert _votes(CTX, 65, 1, LocalKey(9, False)) == "E#2 F-2 Gbb0"
    assert _votes(CTX, 65, 1, None) == "E#2 F-2 Gbb0"
    assert _votes(CTX, 65, 1, LocalKey(0, False), gate=False) == "E#2 F-2 Gbb0"


def test_is_key_degree():
    c, am = LocalKey(0, False), LocalKey(9, True)
    assert [p for p in range(12) if is_key_degree(p, c)] == [0, 2, 4, 5, 7, 9, 11]
    assert [p for p in range(12) if is_key_degree(p, am)] == [0, 2, 4, 5, 6, 7, 8, 9, 11]


def test_degree_keys_count_notes_and_two_pass_gate_can_be_switched_off():
    chords = CTX + [[53, 65], [54, 66], [55, 59, 62, 67]]
    notes = [TwoPassNote(m, k * 500, k * 500 + 500) for k, c in enumerate(chords) for m in c]
    assert len(degree_keys(notes)) == len(notes)
    assert len(spell_two_pass(notes, TwoPassOptions(key_degree_gate=False))) == len(notes)
    assert len(spell_two_pass(notes)) == len(notes)
    # One onset of 12 notes: with unit "notes" each note sees only its neighbours in input order.
    wide = [TwoPassNote(60 + i, 0, 1) for i in range(12)] + [TwoPassNote(60, 1, 2)]
    assert local_keys(wide, "tonic-triad", 1, "notes") != local_keys(wide, "tonic-triad", 1)


def test_degree_keys_fast_path_matches_exact():
    from harness import random_inputs

    from enharmonic.drive import on_notes

    streams = [on_notes(ev) for ev in random_inputs().values()]
    for notes in streams:
        for table, radius in (("tonic-triad", 12), ("temperley", 6), ("tonic-triad", 0)):
            assert local_keys(notes, table, radius, "notes") == local_keys_exact(notes, table, radius, "notes")
