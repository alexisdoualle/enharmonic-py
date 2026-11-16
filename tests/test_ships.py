"""The 14-15 Nov 2026 ships (prod a861ace..a693abd): ports of the matching cases in prod ``test/speller.test.ts``."""

from dataclasses import replace

import pytest

from enharmonic import (
    Speller, TwoPassNote, TwoPassOptions, anchored_side_keys, centred_side_keys, dominant_chords, outward_pairs,
    resolution_dirs, resolve_step, side_keys, spell_two_pass,
)
from enharmonic.drive import on_notes
from enharmonic.engine import LA_PRESET, RT_PRESET, TP_PASS_PRESET, SpellingEngine
from harness import LAB_FIXTURES, fixture_events


def tok(sp):
    if sp is None:
        return "·"
    return sp.step + ("#" * sp.alter if sp.alter > 0 else "b" * -sp.alter if sp.alter < 0 else "")


def load_notes(fid):
    p = LAB_FIXTURES / fid / "events.json"
    if not p.exists():
        pytest.skip(f"missing {p}")
    return on_notes(fixture_events(p))


def chords_to_notes(chords, dur=500):
    return [TwoPassNote(m, k * dur, k * dur + dur) for k, c in enumerate(chords) for m in c]


# ── look-ahead context ─────────────────────────────────────────────────────────


def test_outward_pairs_unison_meet_needs_a_chord():
    # C F# Ab -> G: a bare G is two voices passing; a struck G chord is an aug6.
    def at(chords):
        return [TwoPassNote(m, k, k + 1) for k, c in enumerate(chords) for m in c]

    bare, chord = at([[60, 66, 68], [67]]), at([[60, 66, 68], [55, 59, 67]])
    assert all(p < 0 for p in outward_pairs(bare).partner)
    assert outward_pairs(bare, 8, chord_meet=0).partner[1] == 2
    assert outward_pairs(chord).partner[1] == 2 and outward_pairs(chord).role[1] == 1


def test_side_key_holds_db_major_flat():
    prog = [[49, 61, 65, 68], [54, 61, 66, 70], [56, 60, 63, 68], [49, 61, 65, 68]]
    notes = [TwoPassNote(m, k * 500, k * 500 + 500) for k in range(16) for m in prog[k % 4]]
    x = side_keys(notes)
    assert all(v == -5 for v in x)

    def spell(side):
        s = Speller(look_ahead=True)
        for i, n in enumerate(notes):
            if side:
                s.note_on(n.midi, n.t_on, side_key=x[i])
            else:
                s.note_on(n.midi, n.t_on)
        return " ".join(tok(s.get_spelling(m)) for m in (61, 65, 68))

    assert spell(False) == "C# E# G#"
    assert spell(True) == "Db F Ab"


def test_dominant_names_its_tendency_tones():
    prog = [[53, 56, 62, 72], [52, 56, 62, 71], [43, 57, 60, 64]]
    notes = chords_to_notes(prog)
    doms = dominant_chords(notes)
    assert " ".join(str(d.root) if d else "." for d in doms) == ". . . . 4 4 4 4 . . . ."
    twice = chords_to_notes([[52, 56, 62, 71], [52, 56, 62, 71], [43, 57, 60, 64]])
    assert dominant_chords(twice)[0] is not None
    assert dominant_chords(twice, reach=1)[0] is None

    def spell(use):
        s = Speller(look_ahead=True)
        gs = ""
        for i, n in enumerate(notes):
            d = 0
            for j in range(i + 1, len(notes)):
                if notes[j].t_on != n.t_on:
                    d = resolve_step(n.midi, notes[j].midi)
                    if d:
                        break
            if n.t_on == 1000 and not gs:
                gs = tok(s.get_spelling(56))
            s.note_on(n.midi, n.t_on, d, dominant=doms[i] if use else None)
        return gs

    assert spell(False) == "Ab"
    assert spell(True) == "G#"


def test_resolution_dirs_follow_the_own_voice():
    notes = [TwoPassNote(m, k * 250, k * 250) for k in range(10) for m in (52, 68)]
    notes += [TwoPassNote(70, 2500, 2500), TwoPassNote(69, 2750, 2750)]
    r = resolution_dirs(notes)
    assert r.dir[1] == 1 and r.exact[1] == 1
    assert resolution_dirs(notes, mode="window").dir[1] == 0
    assert resolution_dirs(notes, max=16).dir[1] == 0
    assert resolution_dirs([TwoPassNote(68, 0, 0), TwoPassNote(67, 0, 0), TwoPassNote(69, 1, 1)]).dir[0] == -1


# ── two-pass ───────────────────────────────────────────────────────────────────


def test_struck_triad_third_clair_de_lune():
    notes = load_notes("debussy_clair_de_lune")
    assert (notes[831].midi, notes[831].t_on) == (77, 78750)
    assert tok(spell_two_pass(notes)[831]) == "F"
    assert tok(spell_two_pass(notes, TwoPassOptions(struck_triad_third=False))[831]) == "E#"


def test_centred_side_keys_db_major():
    chords = [[61, 65, 68], [66, 70, 73], [68, 72, 75], [61, 65, 68]]
    notes = [TwoPassNote(m, k * 500, k * 500) for k, c in enumerate(chords) for m in c]
    assert all(v == -5 for v in centred_side_keys(notes))


def test_centred_side_keys_key_return_pathetique():
    notes = load_notes("beethoven_pathetique_adagio")
    on, off = centred_side_keys(notes), centred_side_keys(notes, key_return=False)
    assert (on[814], off[814]) == (4, 4)
    assert (on[992], off[992]) == (-7, 5)
    assert all(v == -7 for v in on[992:1172])


def test_key_signatures_settle_the_six_accidental_tie():
    chords = [[51, 58, 63, 66], [56, 59, 63, 68], [58, 62, 65, 70], [51, 58, 63, 66], [54, 61, 66, 70],
              [49, 56, 61, 65], [54, 58, 61, 66]]
    notes = chords_to_notes(chords)

    def spell(**o):
        return " ".join(tok(p) for p in spell_two_pass(notes, TwoPassOptions(**o)))

    flat = "Eb Bb Eb Gb Ab Cb Eb Ab Bb D F Bb Eb Bb Eb Gb Gb Db Gb Bb Db Ab Db F Gb Bb Db Gb"
    assert spell(key_signatures=[-6] * len(notes)) == flat
    assert spell().startswith("D# A# D# F#")
    assert spell(key_signatures=[6] * len(notes)).endswith("F# A# C# F#")
    assert spell(key_signatures=[-6] * len(notes), key_signature_anchor=False) == spell()
    with pytest.raises(ValueError):
        spell_two_pass(notes, TwoPassOptions(key_signatures=[0]))


def test_key_signatures_far_passage_keeps_inferred_side():
    notes = load_notes("beethoven_pathetique_adagio")
    sigs = [-4] * len(notes)
    anchored = anchored_side_keys(notes, sigs, centred_side_keys(notes))
    assert (anchored[0], anchored[814]) == (-4, 4)
    assert spell_two_pass(notes, TwoPassOptions(key_signatures=sigs))[814:992] == spell_two_pass(notes)[814:992]


# ── key signature (key_tonic / set_key) ────────────────────────────────────────

_LOF_STEP = ["F", "C", "G", "D", "A", "E", "B"]


def _lof_name(n):
    a = (n + 1) // 7
    return _LOF_STEP[n + 1 - 7 * a] + ("#" * a if a > 0 else "b" * -a)


def _play(s, steps, before=None):
    before = before or {}
    t = 0
    out = []
    for i, ch in enumerate(steps):
        if i in before:
            before[i](s)
        for m in ch:
            s.note_on(m, t)
        out.append(" ".join(tok(s.get_spelling(m)) for m in ch))
        t += 500
        for m in ch:
            s.note_off(m, t)
    return " | ".join(out)


@pytest.mark.parametrize("la", [False, True])
def test_first_note_takes_the_signature(la):
    for k in range(-7, 8):
        for n in range(k - 1, k + 6):
            assert _play(Speller(key_tonic=k, look_ahead=la), [[60 + (7 * n) % 12]]) == _lof_name(n)


@pytest.mark.parametrize("la", [False, True])
def test_signature_scales_and_changes(la):
    def keyed(k):
        return Speller(key_tonic=k, look_ahead=la)

    assert _play(keyed(-5), [[61], [63], [65], [66], [68], [70], [72], [73]]) == "Db | Eb | F | Gb | Ab | Bb | C | Db"
    assert _play(keyed(-7), [[59], [61], [63], [64], [66], [68], [70], [71]]) == "Cb | Db | Eb | Fb | Gb | Ab | Bb | Cb"
    assert _play(keyed(0), [[60, 64, 67], [65, 69, 72], [67, 71, 74], [60, 64, 67], [61, 65, 68], [66, 70, 73]],
                 {4: lambda s: s.set_key(-5)}) == "C E G | F A C | G B D | C E G | Db F Ab | Gb Bb Db"
    assert _play(Speller(look_ahead=la), [[60, 64, 67], [68]], {1: lambda s: s.set_key(-3)}) == "C E G | Ab"
    assert _play(keyed(0), [[64], [68], [71], [69]]) == "E | G# | B | A"
    steps = [[60], [63], [67], [66], [68], [61]]
    assert _play(keyed(-3), steps, {3: lambda s: s.set_key(-3)}) == _play(keyed(-3), steps)


def test_signature_picks_the_side_of_the_inferred_key():
    def fold(sig, sides):
        e = SpellingEngine(LA_PRESET)
        e.key_signature(sig)
        for k, x in enumerate(sides):
            e.side_key(x)
            e.note_on(60, k * 500)
            e.note_off(60, k * 500 + 250)
        return e._fold_center - 2

    assert fold(-5, [-5, 7]) == -5
    assert fold(4, [4, -4]) == -4
    assert fold(0, [0, -7]) == 5
    assert fold(-1, [-1, 6]) == -6
    assert fold(7, [7, -4]) == 8    # A♭ major under seven sharps: G♯ major, one fifth from the signature
    assert fold(-7, [-7, 4]) == -8  # E major under seven flats: F♭ major, one fifth from the signature


# ── real-time frame rules ──────────────────────────────────────────────────────


class _EngineSpeller:
    def __init__(self, e):
        self.e = e

    def note_on(self, m, t):
        self.e.note_on(m, t)

    def note_off(self, m, t):
        self.e.note_off(m, t)

    def get_spelling(self, m):
        return self.e.get_spelling(m)


def _cadences():
    def cadence(t):
        return [[48 + m for m in c] for c in ([t, t + 4, t + 7], [t + 5, t + 9, t + 12], [t + 7, t + 11, t + 14],
                                              [t, t + 4, t + 7])]
    return [ch for k in (4, 11, 6, 1) for _ in range(16) for ch in cadence(k)]


def test_frame_twin():
    steps = _cadences()
    last = lambda s: " | ".join(_play(s, steps).split(" | ")[-4:])  # noqa: E731
    assert last(Speller()) == "Db F Ab | Gb Bb Db | Ab C Eb | Db F Ab"
    off = "C# E# G# | F# A# C# | G# B# D# | C# E# G#"
    assert last(_EngineSpeller(SpellingEngine(replace(RT_PRESET, frame_twin=False)))) == off
    assert last(Speller(key_tonic=4)) == off
    assert not LA_PRESET.frame_twin


def test_minor_collection_holds_its_leading_tone():
    seq = [[m] for m in (60, 62, 63, 65, 67, 68, 70, 72, 67, 66, 67)]
    last3 = lambda s: " ".join(_play(s, seq).split(" | ")[-3:])  # noqa: E731
    assert last3(Speller()) == "G F# G"
    assert last3(_EngineSpeller(SpellingEngine(replace(RT_PRESET, minor_leading_tone=False)))) == "G Gb G"
    assert last3(Speller(look_ahead=True)) == "G F# G"
    assert last3(_EngineSpeller(SpellingEngine(replace(LA_PRESET, minor_leading_tone=False)))) == "G Gb G"
    assert not TP_PASS_PRESET.minor_leading_tone


def _read_at_off(opts, ev, start):
    # ev: (t, on, midi); each note is read at its note-off, as the parity bench reads it.
    e = SpellingEngine(opts)
    out = []
    for t, on, m in sorted(ev, key=lambda x: (x[0], x[1])):
        if on:
            e.note_on(m, t)
        else:
            out.append(tok(e.get_spelling(m)))
            e.note_off(m, t)
    return " ".join(out[start:])


def _scale(ms):
    return [x for i, m in enumerate(ms) for x in ((i * 500, True, m), (i * 500 + 400, False, m))]


def _chord(t, ms, dur=400):
    return [(t, True, m) for m in ms] + [(t + dur, False, m) for m in ms]


def test_struck_triad_weighs_two_in_real_time():
    g_major, c_major, b_minor = [67, 69, 71, 72, 74, 76, 78, 79], [60, 62, 64, 65, 67, 69, 71, 72], [59, 61, 62, 64, 66, 67, 69, 71]
    before = replace(RT_PRESET, vertical_weight=1, vertical_pc_triad=False, vertical_own_triad=False, guard_window=3)
    # G major, then a struck Eb major triad: the triad overrules the sharp lean.
    ev = _scale(g_major) + _chord(4000, [51, 55, 58])
    assert _read_at_off(RT_PRESET, ev, 8) == "Eb G Bb"
    assert _read_at_off(before, ev, 8) == "D# G A#"
    ev = _scale(c_major) + _chord(4000, [53, 56, 60])
    assert _read_at_off(RT_PRESET, ev, 8) == "F Ab C"
    assert _read_at_off(before, ev, 8) == "F G# C"
    # A chromatic neighbour beside a held B-D-F# is not its misspelled third.
    ev = _scale(b_minor) + _chord(4000, [47, 59, 62, 66], 1500) + _chord(4500, [77]) + _chord(5000, [78])
    assert _read_at_off(RT_PRESET, ev, 8).split()[:2] == ["E#", "F#"]
    assert _read_at_off(replace(RT_PRESET, vertical_own_triad=False), ev, 8).split()[:2] == ["F", "F#"]
    # A third whose triad already sounds the other third is its neighbour: G# against a ringing F-A-C.
    ev = _scale(c_major) + _chord(4000, [53, 57, 60], 1500) + _chord(4500, [68]) + _chord(5000, [69])
    assert _read_at_off(RT_PRESET, ev, 8).split()[:2] == ["G#", "A"]
    assert _read_at_off(replace(RT_PRESET, vertical_own_triad=False), ev, 8).split()[:2] == ["Ab", "A"]
    assert not LA_PRESET.vertical_own_triad and not TP_PASS_PRESET.vertical_own_triad
