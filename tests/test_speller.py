"""Port of prod ``test/speller.test.ts`` (smoke, resolved surface, onset tolerance, curated fixtures)."""

import pytest

from enharmonic import PitchClass, Speller, resolve_step, spell_two_pass, spell_two_pass_traced
from enharmonic.drive import drive, normalize_events, on_notes
from harness import LAB_FIXTURES, fixture_events

FIXTURES = [
    "bach_wtc1_prelude1_c", "mozart_k545", "chopin_prelude_op28_no4", "chopin_prelude_op28_no15",
    "grieg_death_of_ase", "bach_jesu_meine_freude", "debussy_clair_de_lune",
]


def tok(sp):
    if sp is None:
        return "·"
    return sp.step + ("#" * sp.alter if sp.alter > 0 else "b" * -sp.alter if sp.alter < 0 else "")


def load(fid):
    p = LAB_FIXTURES / fid / "events.json"
    if not p.exists():
        pytest.skip(f"missing {p}")
    return fixture_events(p)


def test_resolve_step_octave_agnostic():
    assert resolve_step(65, 30) == 1
    assert resolve_step(30, 65) == -1
    assert resolve_step(60, 62) == 0


def test_c_major_triad():
    s = Speller()
    for m in (60, 64, 67):
        s.note_on(m, t=0)
    assert [tok(s.get_spelling(m)) for m in (60, 64, 67)] == ["C", "E", "G"]
    assert s.get_spelling(64).octave == 4


def test_reset_scale_key_hint():
    d_major = [PitchClass("D", 0), PitchClass("E", 0), PitchClass("F", 1), PitchClass("G", 0),
               PitchClass("A", 0), PitchClass("B", 0), PitchClass("C", 1)]
    s = Speller()
    s.reset(d_major)
    s.note_on(61, t=0)
    assert tok(s.get_spelling(61)) == "C#"


def test_get_resolved_scale():
    s = Speller()
    s.note_on(60, t=0)
    assert len(s.get_resolved_scale()) == 7


def test_recency_guard_ab_after_g():
    s = Speller()
    for i, m in enumerate([60, 62, 64, 65, 67, 68]):
        s.note_on(m, t=i * 1000)
        if m != 68:
            s.note_off(m)
    assert tok(s.get_spelling(68)) == "Ab"


def test_readme_quickstart():
    nrt = Speller(look_ahead=True)
    nrt.note_on(61, t=0, resolve_dir=1, resolve_exact=1)
    assert tok(nrt.get_spelling(61)) == "C#"
    assert [tok(p) for p in spell_two_pass([(60, 0, 500), (64, 0, 500)])] == ["C", "E"]


def test_key_tonic_and_set_key_run():
    # Values checked against prod TypeScript: a lone 61 stays C#, a struck Db-F-Ab triad is Db.
    s = Speller(key_tonic=-5)
    s.note_on(61, t=0)
    assert tok(s.get_spelling(61)) == "C#"
    s = Speller(key_tonic=-5)
    for m in (61, 65, 68):
        s.note_on(m, t=0)
    assert tok(s.get_spelling(61)) == "Db"
    s.set_key(2)
    s.note_on(62, t=100)
    assert s.get_spelling(62) is not None


@pytest.mark.parametrize("fid", ["mozart_k545"])
def test_two_pass_trace_matches_production(fid):
    notes = on_notes(load(fid))
    prod = spell_two_pass(notes)
    traced = spell_two_pass_traced(notes)
    assert traced.spellings == prod
    assert len(traced.notes) == len(notes)
    assert all(n.forward is not None and n.backward is not None and n.selected is not None for n in traced.notes)


@pytest.mark.parametrize("fid", FIXTURES)
@pytest.mark.parametrize("look_ahead", [False, True])
def test_resolved_surface_holds_each_commit(fid, look_ahead):
    s = Speller(look_ahead=look_ahead)
    for t, typ, midi in load(fid):
        if typ != "on":
            s.note_off(midi)
            continue
        s.note_on(midi, t=t)
        sp = s.get_spelling(midi)
        slot = next(p for p in s.get_resolved_scale() if p.step == sp.step)
        assert slot.alter == sp.alter, f"{fid} t={t}"


def _spread(events):
    t0, k, at, out = None, 0, {}, []
    for t, typ, midi in events:
        if typ != "on":
            out.append((t, typ, midi))
            continue
        if t != t0:
            t0, k = t, 0
            at.clear()
        if midi not in at:
            at[midi] = t + 7 * k
            k += 1
        out.append((at[midi], typ, midi))
    return out


@pytest.mark.parametrize("fid", FIXTURES)
def test_onset_tolerance_spread_chords(fid):
    ev = load(fid)
    exact = [tok(p) for p in drive(Speller(onset_tolerance=50), ev)]
    spread = [tok(p) for p in drive(Speller(onset_tolerance=50), _spread(ev))]
    diff = next((i for i, (a, b) in enumerate(zip(exact, spread)) if a != b), -1)
    assert diff < 0, f"{fid}: onset {diff}: {exact[diff]} vs {spread[diff]}"


@pytest.mark.parametrize("fid", FIXTURES)
def test_curated_fixtures_commit(fid):
    ev = load(fid)
    out = [tok(p) for p in drive(Speller(), ev)]
    assert out and all(t != "·" for t in out)
    out = [tok(p) for p in drive(Speller(look_ahead=True), ev)]
    assert all(t != "·" for t in out)
    notes = on_notes(ev)
    spelled = spell_two_pass(notes)
    assert len(spelled) == len(notes) and all(p is not None for p in spelled)


def test_drive_accepts_dict_events():
    ev = normalize_events([{"t_ms": 0, "type": "respell", "midi": 0}, {"t": 0, "type": "on", "midi": 61},
                           {"t": 100, "type": "off", "midi": 61}])
    assert ev == [(0, "on", 61), (100, "off", 61)]
