"""The diatonic frame's certified fast path must take the same decision as the exact fold, every time.

SpellingEngine._compute_frame_centre decides from approximate (incrementally decayed) counts when the
decision is clear by a tolerance, else defers to _frame_centre_exact, the bit-for-bit TypeScript fold.
This replays the random edge-case streams with both evaluated at every note-on and requires agreement.
"""

from harness import random_inputs

from enharmonic import engine as E
from enharmonic.drive import drive_lof
from enharmonic.speller import Speller


def test_fast_path_matches_exact(monkeypatch):
    fast = E.SpellingEngine._compute_frame_centre
    seen = {"n": 0}

    def checked(self):
        state = (list(self._approx), self._approx_age, self._frame_base, dict(self._frame_memo))
        a = fast(self)
        after = (list(self._approx), self._approx_age, self._frame_base, dict(self._frame_memo))
        self._approx, self._approx_age, self._frame_base, self._frame_memo = state
        b = E.SpellingEngine._frame_centre_exact(self)
        self._approx, self._approx_age, self._frame_base, self._frame_memo = after
        seen["n"] += 1
        assert a == b
        return a

    monkeypatch.setattr(E.SpellingEngine, "_compute_frame_centre", checked)
    for ev in random_inputs().values():
        for make in (Speller, lambda: Speller(look_ahead=True), lambda: Speller(onset_tolerance=30)):
            drive_lof(make(), ev)
    assert seen["n"] > 50_000
