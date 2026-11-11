"""Pure functions: pitch, lof, interval, candidates, scoring, kernel.

Two layers: hand-written examples (the TypeScript doc comments) and an exhaustive comparison against
``parity/goldens/pure.json.gz`` (the same functions evaluated by prod TypeScript over grids that include
negative values and exact .5 ties, where JavaScript and Python arithmetic differ).
"""

import struct

import pytest

from enharmonic import (Pitch, PitchClass, enharmonic_candidates_for, interval_between, interval_label,
                        midi_of, pitch_class_value, pitch_to_string, resolve_exact_step, resolve_step)
from enharmonic._js import js_mod, js_round
from enharmonic.engine import collection_at
from enharmonic.interval import raw_interval_between
from enharmonic.lof import acc, candidates, letter, letter_at, lof_of, pitch_class, spelling_of, C_MAJOR
from enharmonic.scoring import consonance, interval_score
from harness import load_golden

G = load_golden("pure")
NS = list(range(-40, 41))
SPELL = list(range(-15, 20))


def pc(s: str, a: int = 0) -> PitchClass:
    return PitchClass(s, a)


# ── hand-written examples ──

def test_js_helpers():
    assert js_mod(-1, 12) == -1 and js_mod(13, 12) == 1 and js_mod(-13, 12) == -1
    assert js_round(2.5) == 3 and js_round(-2.5) == -2 and js_round(-0.5) == 0 and js_round(0.49999999999999994) == 0


def test_lof_basics():
    assert [lof_of(pc(s)) for s in "FCGDAEB"] == list(range(-1, 6))
    assert lof_of(pc("E", -1)) == -3 and lof_of(pc("E", 1)) == 11
    assert spelling_of(7) == pc("C", 1) and spelling_of(-5) == pc("D", -1)
    assert pitch_class(7) == 1 and pitch_class(-5) == 1


def test_candidates_examples():
    show = lambda m: [f"{p.step}{p.alter}" for p in enharmonic_candidates_for(m)]
    assert show(0) == ["C0", "B1", "D-2"]
    assert show(1) == ["C1", "D-1", "B2"]
    assert show(8) == ["G1", "A-1"]


def test_pitch_helpers():
    assert midi_of(Pitch("C", 0, 4)) == 60
    assert midi_of(Pitch("B", 1, 3)) == 60
    assert pitch_to_string(Pitch("C", 1, 4)) == "C#4" and pitch_to_string(pc("E", -1)) == "Eb"
    assert pitch_class_value(pc("C", -1)) == 11
    with pytest.raises(ValueError):
        midi_of(Pitch("C", -1, -1))


def test_interval_examples():
    assert interval_label(interval_between(pc("C"), pc("G"))) == "P5"
    assert interval_label(interval_between(pc("G"), pc("C"))) == "P4"
    assert interval_label(interval_between(pc("C"), pc("E", -1))) == "m3"
    assert interval_label(interval_between(pc("C"), pc("F", -1))) == "d4"
    assert interval_label(interval_between(pc("C"), pc("C", -1))) == "d8"


def test_scoring_examples():
    assert consonance(0, 1) == 1 and consonance(0, 4) == 1 and consonance(0, 2) == 0
    assert consonance(0, 8) == -1 and consonance(0, 13) == -2
    assert interval_score(0, C_MAJOR) == interval_score(0, list(C_MAJOR))


def test_resolve_step_examples():
    assert resolve_step(65, 30) == 1 and resolve_step(30, 65) == -1 and resolve_step(60, 62) == 0
    assert resolve_exact_step(60, 61) == 1 and resolve_exact_step(60, 73) == 0


# ── exhaustive vs TypeScript ──

def test_lof_primitives_match_ts():
    assert [letter(n) for n in NS] == G["letter"]
    assert [acc(n) for n in NS] == G["acc"]
    assert [pitch_class(n) for n in NS] == G["pitchClass"]
    assert [[spelling_of(n).step, spelling_of(n).alter, lof_of(spelling_of(n))] for n in SPELL] == G["spellingOf"]


def test_candidates_match_ts():
    assert [list(candidates(m)) for m in range(128)] == G["candidates"]
    assert [[f"{p.step}{p.alter}" for p in enharmonic_candidates_for(m)] for m in range(12)] == G["enharmonicCandidatesFor"]


def test_letter_at_and_collection_match_ts():
    cs = G["letterAtCs"]
    assert [[letter_at(s, c) for c in cs] for s in range(7)] == G["letterAt"]
    assert [[lof_of(p) for p in collection_at(c)] for c in cs] == G["collectionAt"]


def test_scoring_matches_ts():
    assert [[consonance(a, b) for b in NS] for a in NS] == G["consonance"]
    assert [[interval_score(n, sc) for n in SPELL] for sc in G["scales"]] == G["intervalScore"]


def test_intervals_match_ts():
    for a, b, q, num, label in G["intervals"]:
        pa, pb = spelling_of(a), spelling_of(b)
        assert raw_interval_between(pa, pb) == (q, num), (a, b)
        assert interval_label(interval_between(pa, pb)) == label, (a, b)


def test_pitch_functions_match_ts():
    for n, octv, m, s, pcv in G["midiOf"]:
        sp = spelling_of(n)
        p = Pitch(sp.step, sp.alter, octv)
        if m == "RangeError":
            with pytest.raises(ValueError):
                midi_of(p)
        else:
            assert midi_of(p) == m
        assert pitch_to_string(p) == s and pitch_class_value(p) == pcv


def test_resolve_steps_match_ts():
    bs = [0, 1, 11, 12, 13, 59, 60, 61, 62, 127]
    assert [[resolve_step(a, b) for b in bs] for a in range(128)] == G["resolveStep"]
    assert [[resolve_exact_step(a, b) for b in bs] for a in range(128)] == G["resolveExactStep"]


def test_frame_decay_bit_identical():
    # The diatonic frame's recency weight: Math.pow(0.5, 1/64) in V8 must equal Python's to the bit.
    assert struct.pack(">d", 0.5 ** (1 / 64)) == struct.pack(">d", G["decay64"])
