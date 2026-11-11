"""Decision-trace parity on a small subset: every candidate's score breakdown after every note-on (rt, la),
and the two-pass forward/backward/merge trace. When a spelling diverges, these pin down which mechanism
(base, guard, look-ahead, double-accidental, vertical, leash, lean) and which pass disagreed first."""

import pytest

from enharmonic import Speller, spell_two_pass_traced
from enharmonic.drive import drive_lof, on_notes
from enharmonic.lof import lof_of
from harness import LAB_FIXTURES, fixture_events, load_golden, random_inputs

G = load_golden("traces")["traces"]


class _Hooked:
    """Speller wrapper recording the engine's decision after each note-on (export.mts does the same)."""

    def __init__(self, la: bool):
        self.s = Speller(look_ahead=la)
        self.look_ahead = la
        self.rows = []

    def note_on(self, midi, t=None, resolve_dir=0, resolve_exact=0):
        self.s.note_on(midi, t, resolve_dir, resolve_exact)
        d = self.s.engine.decision()
        self.rows.append(None if d is None else [
            lof_of(d.chosen), [lof_of(p) for p in d.scale],
            [[lof_of(c.c), c.base, c.guard_delta, c.la_delta, c.da_delta, c.vert_delta, c.side_delta, c.lean_delta]
             for c in d.candidates],
        ])

    def note_off(self, midi, t=None):
        self.s.note_off(midi, t)

    def get_lof(self, midi):
        return self.s.get_lof(midi)


def _events(iid):
    if iid.startswith("random/"):
        return random_inputs()[iid.split("/", 1)[1]]
    p = LAB_FIXTURES / iid / "events.json"
    if not p.exists():
        pytest.skip(f"missing {p}")
    return fixture_events(p)


_IDS = sorted({k.split("|")[0] for k in G})


@pytest.mark.parametrize("iid", _IDS)
@pytest.mark.parametrize("mode", ["rt", "la"])
def test_decision_trace(iid, mode):
    want = G[f"{iid}|{mode}"]
    h = _Hooked(mode == "la")
    drive_lof(h, _events(iid))
    assert len(h.rows) == len(want)
    for i, (a, b) in enumerate(zip(h.rows, want)):
        assert a == b, f"{iid} {mode}: first divergent decision at note-on {i}:\n python {a}\n ts     {b}"


@pytest.mark.parametrize("iid", _IDS)
def test_two_pass_trace(iid):
    want = G[f"{iid}|tp"]
    tr = spell_two_pass_traced(on_notes(_events(iid)))
    lof = lambda p: None if p is None else lof_of(p)
    assert [lof(n.forward) for n in tr.notes] == want["forward"]
    assert [lof(n.backward) for n in tr.notes] == want["backward"]
    assert [n.agrees for n in tr.notes] == want["agrees"]
    assert [n.source for n in tr.notes] == want["source"]
    assert [None if n.frame is None else n.frame.fold_centre for n in tr.notes] == want["foldCentre"]
    assert tr.first_stable == want["firstStable"] and tr.last_stable == want["lastStable"]
