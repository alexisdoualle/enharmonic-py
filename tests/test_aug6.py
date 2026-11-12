"""The aug6 outward rule (mirrors prod test/speller.test.ts 'aug6 outward convergence')."""

from enharmonic import Speller, TwoPassNote, TwoPassOptions, outward_pairs, spell_two_pass


def _tok(p):
    return p.step + ("#" * p.alter if p.alter > 0 else "b" * -p.alter)


def _run(chords, at, hi, lo, ctx):
    s = Speller(look_ahead=True)
    for k, c in enumerate(chords):
        if k:
            for m in chords[k - 1]:
                s.note_off(m, k * 500)
        for m in c:
            if ctx and k == at and m in (hi, lo):
                s.note_on(m, k * 500, outward_partner=lo if m == hi else hi, outward_role=1 if m == hi else -1)
            else:
                s.note_on(m, k * 500)
    return " ".join(_tok(s.get_spelling(m)) for m in chords[-1])


C_MINOR = [[48, 60, 63, 67], [53, 60, 65, 68], [55, 59, 62, 67], [48, 60, 63, 67]]


def _notes(chords, dt=1):
    return [TwoPassNote(m, k * dt, k * dt + dt) for k, c in enumerate(chords) for m in c]


def test_german_sixth_rising_tone_is_sharp_four():
    ger6 = C_MINOR + [[56, 60, 63, 66]]
    assert _run(ger6, 4, 66, 56, True) == "Ab C Eb F#"
    assert _run(ger6, 4, 66, 56, False) == "Ab C Eb Gb"


def test_key_gate_keeps_a_rising_diatonic_seventh():
    a_major = [[45, 57, 61, 64], [50, 57, 62, 66], [52, 56, 59, 64], [45, 57, 61, 64], [50, 52, 56, 59]]
    assert _run(a_major, 4, 50, 52, True) == "D E G# B"


def test_outward_pairs():
    op = outward_pairs(_notes([[56, 60, 63, 66], [55, 59, 62, 67]]))
    assert (op.partner[3], op.role[3]) == (0, 1)
    assert (op.partner[0], op.role[0]) == (3, -1)
    assert all(p < 0 for p in outward_pairs(_notes([[56, 60, 63, 66], [56, 60, 63, 65], [55, 59, 62, 67]])).partner)
    assert all(p < 0 for p in outward_pairs(_notes([[56, 63, 66], [55, 62, 67]])).partner)


def test_two_pass_options():
    notes = _notes(C_MINOR + [[56, 60, 63, 66], [55, 59, 62, 67]], 500)
    assert len(spell_two_pass(notes, TwoPassOptions(aug6_outward=False, aug6_wedge=False))) == len(notes)
    p = spell_two_pass(notes)[19]
    assert (p.step, p.alter) == ("F", 1)
