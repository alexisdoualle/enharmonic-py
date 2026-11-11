"""SpellingEngine: the shipped speller (port of ``src/engine.ts``).

One engine; the latency modes are presets over it (:data:`RT_PRESET`, :data:`LA_PRESET`,
:data:`TP_PASS_PRESET`). No key detection anywhere. See the TypeScript source for the full design notes;
the comments here mark the places where the port has to take care to reproduce JavaScript behaviour
(Map/Set insertion order, ``??`` on values that may be 0, ``Math.round``, floating-point summation order).

Two side-substrates coexist, chosen by ``frame_mode``:

- ``'diatonic'`` (streaming tiers, RT/LA): the 7 slots ARE a coverage collection over recent raw pitch
  classes, recomputed every onset, with keep-alive for committed chromatics. ``note_on`` returns at the
  diatonic commit, so the fold / collision repair / leash-anchor code never runs.
- ``'mean'`` (two-pass per-direction passes): the slots drift and the side is corrected after the fact by
  :meth:`SpellingEngine._maybe_fold`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable, Literal, Optional

from .lof import COMMA, C_MAJOR, LETTER_ORDER, acc, candidates, letter, letter_at, lof_of, pitch_class, spelling_of
from .pitch import Pitch, PitchClass
from .scoring import CONSONANCE_BY_DISTANCE

FoldMode = Literal["clamp", "economy", "off"]


def _is_misspelled_third(a: int, b: int) -> bool:
    """A diminished fourth (8 apart) or augmented second (9 apart) on the line of fifths."""
    d = abs(a - b)
    return d == 8 or d == 9


def _nearest_candidates(pc: int) -> tuple[tuple[int, int], ...]:
    """(n, |accidental|) of every [-2, +2] spelling of ``pc``, in letter order C..B."""
    out = []
    for s in LETTER_ORDER:
        raw = (pc - pitch_class(s - 1)) % 12
        alter = raw - 12 if raw > 6 else raw
        if abs(alter) > 2:
            continue
        n = s - 1 + 7 * alter
        out.append((n, abs(acc(n))))
    return tuple(out)


#: Per pitch class, the spellings :func:`_spell_nearest` chooses among (hoisted out of the hot loop).
_NEAREST: tuple[tuple[tuple[int, int], ...], ...] = tuple(_nearest_candidates(pc) for pc in range(12))


def _spell_nearest(pc: int, c: float) -> int:
    """The [-2, +2] spelling of pitch class ``pc`` nearest centre ``c``; ties go to the first letter C..B."""
    best = 0
    best_d = math.inf
    for n, _ in _NEAREST[pc]:
        if abs(n - c) < best_d:
            best_d = abs(n - c)
            best = n
    return best


#: |accidental| of the nearest spelling of each pitch class, per centre c (a float, memoised by value: the
#: fold's centres are sums of seven ints over 7, plus or minus a comma, so few distinct values occur).
_NEAREST_COST: dict[float, tuple[int, ...]] = {}

#: Per pitch class, the chromatic-sharp-lean target: the sharpest candidate within one accidental.
_LEAN_TARGET: tuple[Optional[int], ...] = tuple(
    max((n for n in candidates(pc) if abs(acc(n)) <= 1), default=None) for pc in range(12))

# Pure functions of an INTEGER centre, memoised (the diatonic frame centre is always an int).
_FRAME_SCALE: dict[int, tuple[int, ...]] = {}
_COLLECTION_PCS: dict[int, frozenset[int]] = {}


#: Certified fast path of the diatonic frame (see _compute_frame_centre): the approximate counts may differ
#: from the exact fold by ~1e-11 at most (32 onsets of drift on values below ~1e3); a decision is taken from
#: them only when every comparison it rests on clears this tolerance, otherwise the exact fold decides.
_FRAME_TOL = 1e-6
#: Window moves between re-syncs of the approximate counts to the exact fold.
_FRAME_RESYNC = 32

#: Per residue r = c mod 12, the pitch classes of line-of-fifths positions c-3 .. c+3, in that order.
_COVERAGE_PCS: tuple[tuple[int, ...], ...] = tuple(tuple((7 * (r + i)) % 12 for i in range(-3, 4)) for r in range(12))


def _frame_scale(c: int) -> tuple[int, ...]:
    """``letter_at(s, c)`` for the seven slots s = 0..6 (fifths order)."""
    t = _FRAME_SCALE.get(c)
    if t is None:
        t = _FRAME_SCALE[c] = tuple(letter_at(s, c) for s in range(7))
    return t


def _collection_pcs(c: int) -> frozenset[int]:
    t = _COLLECTION_PCS.get(c)
    if t is None:
        t = _COLLECTION_PCS[c] = frozenset(pitch_class(n) for n in _frame_scale(c))
    return t


def collection_at(c: float) -> list[PitchClass]:
    """The 7-letter diatonic collection whose line-of-fifths centre is ``c`` (C major = 2)."""
    return [spelling_of(letter_at(s, c)) for s in LETTER_ORDER]


# ── Options + presets ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EngineOptions:
    """The full mechanism switchboard (TS ``EngineOptions``). Defaults are the engine constructor's
    ``??`` defaults, NOT a preset. See the TypeScript source for each field's documentation."""

    recency_guard: int = 2
    guard_window: int = 3
    fold: FoldMode = "clamp"
    fold_center: int = 3
    fold_radius: int = 7
    fold_window: int = 24
    fold_margin: int = 7
    fold_max_chroma: int = 0
    fold_debounce: int = 8
    side_weight: int = 0
    side_radius: int = 6
    anchor_window: int = 32
    look_ahead: bool = False
    look_ahead_weight: int = 2
    leading_tone_boost: int = 0
    double_acc_penalty: int = 0
    vertical_weight: int = 0
    collision_repair: bool = False
    fold_slot_recency: int = 0
    fold_repair_scale: bool = False
    fold_economy_gate: bool = False
    frame_mode: Literal["mean", "diatonic"] = "mean"
    frame_half_life: float = 64
    frame_margin: float = 1
    frame_keep_alive: bool = True
    frame_side: Literal["continuity", "drift"] = "continuity"
    frame_window: int = 128
    chromatic_sharp_lean: int = 0
    onset_tolerance: float = 0
    onset_revise: bool = False
    guard_raise_line: bool = False
    ornament_ms: float = 0
    vertical_pc_triad: bool = False


#: REAL-TIME tier (``Speller()``).
RT_PRESET = EngineOptions(
    recency_guard=2, guard_window=3,
    fold="clamp", fold_center=3, fold_radius=7,
    side_weight=1, side_radius=6, anchor_window=32,
    frame_mode="diatonic", vertical_weight=1, chromatic_sharp_lean=1, onset_revise=True,
    ornament_ms=80,
)

#: LOOK-AHEAD tier (``Speller(look_ahead=True)``).
LA_PRESET = EngineOptions(
    recency_guard=2, guard_window=7,
    fold="clamp", fold_center=3, fold_radius=7, fold_debounce=8,
    look_ahead=True, look_ahead_weight=2, leading_tone_boost=1,
    double_acc_penalty=2, collision_repair=True,
    frame_mode="diatonic", onset_revise=True, guard_raise_line=True,
    side_weight=1, side_radius=8, anchor_window=32,
    ornament_ms=80,
    vertical_weight=2, vertical_pc_triad=True,
)

#: TWO-PASS per-direction pass (:func:`enharmonic.spell_two_pass`).
TP_PASS_PRESET = EngineOptions(
    recency_guard=2, guard_window=7,
    fold="economy", fold_window=24, fold_margin=7, fold_max_chroma=0, fold_debounce=8,
    look_ahead=True, look_ahead_weight=2,
    double_acc_penalty=2, vertical_weight=1, collision_repair=True, guard_raise_line=True,
)


# ── Decision trace (introspection only) ────────────────────────────────────────


@dataclass(frozen=True)
class DecisionCandidate:
    """One candidate's scoring breakdown. The argmax is the sum of ``base`` and the six deltas."""

    c: PitchClass
    base: int
    guard_delta: int
    la_delta: int
    da_delta: int
    vert_delta: int
    side_delta: int
    lean_delta: int


@dataclass(frozen=True)
class Decision:
    scale: list[PitchClass]
    candidates: list[DecisionCandidate]
    chosen: PitchClass


class _Pending:
    """A chromatic committed within the ornament window, not yet kept (mutable: re-decisions update n)."""

    __slots__ = ("n", "t")

    def __init__(self, n: int, t: float) -> None:
        self.n = n
        self.t = t


# Raw decision row: (n, base, guard, la, da, vert, side, lean).
_Row = tuple[int, int, int, int, int, int, int, int]


class SpellingEngine:
    def __init__(self, opts: EngineOptions = EngineOptions()) -> None:
        o = opts
        self._recency_guard = o.recency_guard
        self._guard_window = o.guard_window
        self._fold = o.fold
        self._fold_center = o.fold_center  # mutable via set_key
        self._fold_radius = o.fold_radius
        self._fold_window = o.fold_window
        self._fold_margin = o.fold_margin
        self._fold_max_chroma = o.fold_max_chroma
        self._fold_debounce = o.fold_debounce
        self._side_weight = o.side_weight
        self._side_radius = o.side_radius
        self._anchor_window = o.anchor_window
        self._look_ahead_on = o.look_ahead
        self._look_ahead_weight = o.look_ahead_weight
        self._leading_tone_boost = o.leading_tone_boost
        self._double_acc_penalty = o.double_acc_penalty
        self._vertical_weight = o.vertical_weight
        self._collision_repair = o.collision_repair
        self._fold_slot_recency = o.fold_slot_recency
        self._fold_repair_scale = o.fold_repair_scale
        self._fold_economy_gate = o.fold_economy_gate
        self._frame_mode = o.frame_mode
        self._frame_half_life = o.frame_half_life
        self._frame_margin = o.frame_margin
        self._frame_keep_alive = o.frame_keep_alive
        self._frame_side = o.frame_side
        self._frame_window = o.frame_window
        self._chromatic_sharp_lean = o.chromatic_sharp_lean
        self._onset_tolerance = o.onset_tolerance
        self._onset_revise = o.onset_revise
        self._guard_raise_line = o.guard_raise_line
        self._ornament_ms = o.ornament_ms
        self._vertical_pc_triad = o.vertical_pc_triad
        # Math.pow(0.5, 1 / halfLife): bit-identical to V8 for the default 64 (checked in the parity tests).
        self._decay = math.pow(0.5, 1 / self._frame_half_life)
        self._window_cap = max(self._fold_window, self._frame_window) if self._frame_mode == "diatonic" else self._fold_window
        # The frame's decay weights by onset age, built by the same repeated multiplication as TS (`w *= decay`)
        # so every weight is bit-identical; index k = k-th most recent onset in the window.
        w = 1.0
        weights = [w]
        for _ in range(self._window_cap + 1):
            w *= self._decay
            weights.append(w)
        self._weights = weights
        self._init_state()

    def _init_state(self) -> None:
        # Insertion-ordered dicts stand in for JS Map/Set (same iteration order, same "re-set keeps position").
        self._pending: dict[int, _Pending] = {}
        self._cur_onset_t: dict[int, float] = {}
        self._cur_onset_dirs: dict[int, int] = {}
        self._cur_onset_exact: dict[int, int] = {}
        self._guard_before: list[Optional[tuple[int, int]]] = [None] * 7
        self._guard_memo: Optional[list[Optional[tuple[int, int]]]] = None
        self._kept: dict[int, int] = {}
        self._last_commit: Optional[int] = None
        self._scale: list[int] = list(C_MAJOR)
        self._active: dict[int, int] = {}
        self._held: dict[int, int] = {}
        self._last_by_letter: list[Optional[tuple[int, int]]] = [None] * 7
        self._onset = -1
        self._last_t: float = math.nan
        self._pc_window: list[list[int]] = []
        self._cur_onset_pcs: list[int] = []
        self._cur_onset_midis: dict[int, None] = {}
        self._pending_dir = 0
        self._pending_count = 0
        self._committed_lof: list[int] = []
        self._anchor: float = 1
        self._collection_centre = 2
        self._fold_centre: float = 2
        self._frame_centre = 2
        self._last_decision: Optional[tuple[tuple[int, ...], list[_Row], int]] = None
        # Pitch-class counts of _pc_window + _cur_onset_pcs (the 'mean' fold's "heard"), kept incrementally.
        self._heard_counts: list[int] = [0] * 12
        # Per-onset cache for the diatonic frame (the window only changes at a new onset); see
        # _compute_frame_centre. None = rebuild.
        self._frame_memo: dict[int, float] = {}
        self._base_scale: list[int] = []
        self._base_memo: dict[int, int] = {}
        self._frame_base: Optional[list[float]] = None  # this onset's counts with no current-onset notes
        # Approximate _frame_base, updated incrementally (x decay per window move); re-synced to the exact
        # fold every _FRAME_RESYNC moves and whenever the exact fold runs anyway.
        self._approx: list[float] = [0.0] * 12
        self._approx_age = 0
        # Per pitch class, the window onsets (by sequence number, oldest first) holding it, one entry per
        # occurrence; _win_seq is the newest window onset's number. Kept incrementally as the window moves.
        self._occ: list[list[int]] = [[] for _ in range(12)]
        self._win_seq = 0

    # ── note-on ──

    def note_on(self, midi: int, t: Optional[float] = None, resolve_dir: int = 0, resolve_exact: int = 0) -> None:
        tol = self._onset_tolerance
        if t is None:
            new_onset = True
        elif tol > 0:
            # NaN lastT: new onset (any comparison with NaN is False, as in JS).
            ct = self._cur_onset_t.get(midi)
            new_onset = (not (t - self._last_t <= tol)) or (ct is not None and t != ct)
        else:
            new_onset = t != self._last_t
        if new_onset:
            self._cur_onset_midis.clear()
            self._cur_onset_t.clear()
            self._cur_onset_dirs.clear()
            self._cur_onset_exact.clear()
            if self._onset_revise:
                self._guard_before = list(self._last_by_letter)
            if self._cur_onset_pcs:
                self._pc_window.append(self._cur_onset_pcs)
                self._win_seq += 1
                occ = self._occ
                seq = self._win_seq
                for pc in self._cur_onset_pcs:
                    occ[pc].append(seq)
                evicted = self._pc_window.pop(0) if len(self._pc_window) > self._window_cap else None
                if evicted is not None:
                    heard = self._heard_counts
                    for pc in evicted:
                        heard[pc] -= 1
                        occ[pc].pop(0)
                if self._frame_mode == "diatonic":
                    A = self._approx
                    d = self._decay
                    for i in range(12):
                        A[i] *= d
                    w1 = self._weights[1]
                    for pc in self._cur_onset_pcs:
                        A[pc] += w1
                    if evicted is not None:
                        w_old = self._weights[self._window_cap + 1]
                        for pc in evicted:
                            A[pc] = A[pc] - w_old if occ[pc] else 0.0  # an absent class is exactly 0
                    self._approx_age += 1
                self._cur_onset_pcs = []
                self._frame_memo = {}
                self._frame_base = None
            if t is not None:
                self._last_t = t
            self._onset += 1
        pc = midi % 12
        self._cur_onset_pcs.append(pc)
        self._heard_counts[pc] += 1
        self._cur_onset_midis[midi] = None
        if t is not None and midi not in self._cur_onset_t:
            self._cur_onset_t[midi] = t
        self._cur_onset_dirs[midi] = resolve_dir
        self._cur_onset_exact[midi] = resolve_exact

        if self._pending and t is not None:
            for m, p in list(self._pending.items()):
                if self._last_t - p.t >= self._ornament_ms:
                    del self._pending[m]
                    self._keep(p.n)
        if self._frame_mode == "diatonic":
            prev_centre = self._frame_centre
            self._frame_centre = self._compute_frame_centre()
            self._fold_centre = self._frame_centre
            self._collection_centre = self._frame_centre
            self._anchor = self._frame_centre
            if self._frame_keep_alive and self._frame_centre != prev_centre:
                self._kept.clear()
                self._pending.clear()
            scale = self._scale
            scale[:] = _frame_scale(self._frame_centre)
            if self._frame_keep_alive:
                for s, n in list(self._kept.items()):
                    if scale[s] != n:
                        scale[s] = n
                    else:
                        del self._kept[s]
                for m, p in self._pending.items():
                    if m in self._cur_onset_midis:
                        scale[letter(p.n)] = p.n

        if self._side_weight > 0 and self._frame_mode != "diatonic":
            self._anchor = self._diatonic_anchor()

        best, rows = self._decide(midi, resolve_dir, resolve_exact, None, True)
        if best is None:
            return
        # Slot order (fifths); decision() reorders C..B only when asked.
        self._last_decision = (tuple(self._scale), rows, best)
        self._active[midi] = best
        self._held[midi] = self._held.get(midi, 0) + 1
        self._last_commit = best
        lb = (best + 1) % 7  # letter(best)
        self._last_by_letter[lb] = (self._onset, best)
        if self._frame_mode == "diatonic":
            self._committed_lof.append(best)
            if len(self._committed_lof) > self._anchor_window:
                self._committed_lof.pop(0)
            if self._frame_keep_alive:
                if best == _frame_scale(self._frame_centre)[lb]:
                    self._kept.pop(lb, None)
                elif self._ornament_ms > 0 and t is not None:
                    self._pending[midi] = _Pending(best, self._last_t)
                else:
                    self._kept[lb] = best
            if self._onset_revise:
                self._revise_onset(midi)
            return

        self._scale[letter(best)] = best
        if self._side_weight > 0:
            self._committed_lof.append(best)
            if len(self._committed_lof) > self._anchor_window:
                self._committed_lof.pop(0)
            self._update_collection()
        if self._collision_repair:
            self._repair_collision(best)
        if self._fold != "off":
            self._maybe_fold()

    # ── scoring ──

    def _decide(self, midi: int, resolve_dir: int, resolve_exact: int, self_midi: Optional[int],
                trace: bool) -> tuple[Optional[int], list[_Row]]:
        # Hot path: letter(n) = (n + 1) % 7, acc(n) = (n + 1) // 7 and pitch_class(n) = (7 * n) % 12 are
        # inlined, and interval_score is inlined over CONSONANCE_BY_DISTANCE (integer sums, order-free).
        scale = self._scale
        la_on = self._look_ahead_on and resolve_dir != 0
        la_step: Optional[int] = None
        la_target: Optional[int] = None
        la_weight = self._look_ahead_weight
        if la_on:
            target_pc = (midi + resolve_dir) % 12
            for s in LETTER_ORDER:
                if (7 * scale[s]) % 12 == target_pc:
                    la_target = s
                    break
            if la_target is not None:
                la_step = (la_target - 2 * resolve_dir) % 7
                if resolve_dir == 1 and (scale[la_target] + 1) // 7 == 0:
                    la_weight += self._leading_tone_boost

        vw = self._vertical_weight
        co_sounding: list[int] = []
        in_perfect_triad = False
        if vw > 0:  # (the triad test only matters when the vertical term is on)
            active = self._active
            co_sounding = [v for m, v in active.items() if m != self_midi]
            if self._vertical_pc_triad:
                pcs = {m % 12 for m in active if m != self_midi}
                pcs.add(midi % 12)
                # some pair a fifth apart by pitch class  <=>  (b - a) % 12 == 7 for some a, b
                for a in pcs:
                    if (a + 7) % 12 in pcs:
                        in_perfect_triad = True
                        break
            elif len(co_sounding) > 1:
                # some pair a fifth apart on the line of fifths  <=>  v + 1 present for some v
                vals = set(co_sounding)
                for v in vals:
                    if v + 1 in vals:
                        in_perfect_triad = True
                        break

        lean_target: Optional[int] = None
        if self._chromatic_sharp_lean > 0:
            centre = self._frame_centre if self._frame_mode == "diatonic" else self._collection_centre
            if (midi % 12) not in _collection_pcs(centre):
                lean_target = _LEAN_TARGET[midi % 12]

        rows: list[_Row] = []
        best: Optional[int] = None
        best_score = -math.inf
        guard_mem = self._guard_memo if self._guard_memo is not None else self._last_by_letter
        rg = self._recency_guard
        gw = self._guard_window
        onset = self._onset
        raise_on = self._guard_raise_line and resolve_exact == 1
        dap = self._double_acc_penalty
        side_w = self._side_weight
        anchor = self._anchor
        side_r = self._side_radius
        lean = self._chromatic_sharp_lean
        vert_on = vw > 0 and in_perfect_triad
        cons = CONSONANCE_BY_DISTANCE
        # interval_score depends only on (n, scale); the onset revision re-decides the same notes against
        # the same scale, so memoise it until the scale changes.
        base_memo = self._base_memo
        if scale != self._base_scale:
            self._base_scale = list(scale)
            base_memo.clear()
        for n in candidates(midi):
            ln_ = (n + 1) % 7
            base = base_memo.get(n)
            if base is None:
                base = 0
                for x in scale:
                    d = n - x if n >= x else x - n
                    base += cons[d] if d < 13 else -2
                x = scale[ln_]
                d = n - x if n >= x else x - n
                base -= cons[d] if d < 13 else -2
                base_memo[n] = base
            last = guard_mem[ln_]
            if (rg and last is not None and last[1] != n and onset - last[0] <= gw
                    and not (raise_on and n - last[1] == 7)):
                guard_delta = -rg
            else:
                guard_delta = 0
            if la_on:
                la_delta = la_weight if ln_ == la_step else (-la_weight if ln_ == la_target else 0)
            else:
                la_delta = 0
            a = (n + 1) // 7
            da_delta = -dap if (a >= 2 or a <= -2) else 0
            mis_thirds = 0
            if vert_on:
                for x in co_sounding:
                    d = n - x
                    if d == 8 or d == 9 or d == -8 or d == -9:
                        mis_thirds += 1
            vert_delta = -vw * mis_thirds
            side_delta = (-side_w * max(0, abs(n - anchor) - side_r)) if side_w > 0 else 0
            lean_delta = lean if (lean_target is not None and -1 <= a <= 1 and n == lean_target) else 0
            s = base + guard_delta + la_delta + da_delta + vert_delta + side_delta + lean_delta
            if trace:
                rows.append((n, base, guard_delta, la_delta, da_delta, vert_delta, side_delta, lean_delta))
            if s > best_score:
                best_score = s
                best = n
        return best, rows

    # ── onset buffer ──

    def _revise_onset(self, just: int) -> None:
        live = self._scale
        if self._pending:
            self._scale = list(self._scale)
            for p in self._pending.values():
                ln_ = letter(p.n)
                k = self._kept.get(ln_)
                self._scale[ln_] = k if k is not None else _frame_scale(self._frame_centre)[ln_]
        try:
            self._revise_onset_notes(just)
        finally:
            self._scale = live

    def _revise_onset_notes(self, just: int) -> None:
        for m in self._cur_onset_midis:
            if m == just or m not in self._active:
                continue
            old = self._active[m]
            self._guard_memo = self._guard_before
            best, _ = self._decide(m, self._cur_onset_dirs.get(m, 0), self._cur_onset_exact.get(m, 0), m, False)
            self._guard_memo = None
            if best is None or best == old:
                continue
            self._active[m] = best
            self._last_by_letter[letter(best)] = (self._onset, best)
            cl = self._committed_lof
            for i in range(len(cl) - 1, -1, -1):  # lastIndexOf
                if cl[i] == old:
                    cl[i] = best
                    break
            if self._frame_keep_alive:
                if self._kept.get(letter(old)) == old:
                    del self._kept[letter(old)]
                pend = self._pending.get(m)
                if pend is not None:
                    pend.n = best
                elif best != _frame_scale(self._frame_centre)[letter(best)]:
                    self._kept[letter(best)] = best

    # ── 'mean' frame machinery ──

    def _repair_collision(self, just: int) -> None:
        pc = (7 * just) % 12  # pitch_class(just)
        own = (just + 1) % 7  # letter(just)
        scale = self._scale
        sounding = None
        for s in range(7):
            if s == own or (7 * scale[s]) % 12 != pc:
                continue
            if sounding is None:  # built lazily: a collision is rare
                sounding = {(n + 1) % 7 for n in self._active.values()}
            if s in sounding:
                continue
            for alt in (0, -1, 1, -2, 2):
                n = s - 1 + 7 * alt
                if (7 * n) % 12 != pc:
                    scale[s] = n
                    break

    def _maybe_fold(self) -> None:
        entries = [(s, self._scale[s]) for s in LETTER_ORDER]
        if self._fold_slot_recency > 0:
            fresh = []
            for s, v in entries:
                last = self._last_by_letter[s]
                if last is not None and self._onset - last[0] <= self._fold_slot_recency:
                    fresh.append((s, v))
            if fresh:
                entries = fresh
        if self._fold_repair_scale:
            vals = [v for _, v in entries]
            med = self._median(vals)
            best_k, best_cov = med, -1
            for k in range(med - 6, med + 7):
                cov = sum(1 for v in vals if abs(v - k) <= 3)
                if cov > best_cov or (cov == best_cov and abs(k - med) < abs(best_k - med)):
                    best_cov = cov
                    best_k = k
            entries = [(s, v if abs(v - best_k) <= 3 else letter_at(s, best_k)) for s, v in entries]
        total = 0
        for _, v in entries:
            total += v
        c_frame = total / len(entries)  # int / int: one correctly rounded division, as in JS
        self._fold_centre = c_frame

        favoured = 0
        if self._fold == "clamp":
            if c_frame > self._fold_center + self._fold_radius:
                favoured = -COMMA
            elif c_frame < self._fold_center - self._fold_radius:
                favoured = +COMMA
            if favoured != 0 and self._fold_economy_gate:
                heard = self._heard_counts
                if any(heard):
                    if self._cost_at(heard, c_frame + favoured) > self._cost_at(heard, c_frame):
                        favoured = 0
        else:
            heard = self._heard_counts
            if not any(heard):
                return
            vetoed = self._fold_max_chroma > 0 and sum(1 for k in heard if k) > self._fold_max_chroma
            here = self._cost_at(heard, c_frame)
            up = self._cost_at(heard, c_frame + COMMA)
            dn = self._cost_at(heard, c_frame - COMMA)
            if up < here - self._fold_margin and up <= dn:
                favoured = +COMMA
            elif dn < here - self._fold_margin:
                favoured = -COMMA
            if vetoed:
                favoured = 0

        if favoured != 0 and favoured == self._pending_dir:
            self._pending_count += 1
        else:
            self._pending_dir = favoured
            self._pending_count = 0 if favoured == 0 else 1
        if favoured == 0 or self._pending_count < self._fold_debounce:
            return
        self._pending_dir = 0
        self._pending_count = 0

        new_centre = c_frame + favoured
        for s in range(7):
            self._scale[s] = letter_at(s, new_centre)

    @staticmethod
    def _cost_at(heard: list[int], c: float) -> int:
        """Accidentals spent spelling every heard note nearest ``c``. ``heard`` holds COUNTS per pitch class
        (the sum is of integers, so grouping by pitch class leaves it unchanged)."""
        costs = _NEAREST_COST.get(c)
        if costs is None:
            costs = _NEAREST_COST[c] = tuple(abs(acc(_spell_nearest(pc, c))) for pc in range(12))
        total = 0
        for k, a in zip(heard, costs):
            total += k * a
        return total

    # ── 'diatonic' frame ──

    def _compute_frame_centre(self) -> int:
        cur = self._cur_onset_pcs
        if not cur and not self._pc_window:
            return self._frame_centre  # TS: total == 0 (window entries are never empty)
        if self._approx_age >= _FRAME_RESYNC:
            return self._frame_centre_exact()
        # Fast path: the same decision from approximate counts, taken only when it is certain, i.e. one
        # residue has the strictly highest coverage by more than _FRAME_TOL and the keep-prev margin test
        # is not within _FRAME_TOL of its threshold. Otherwise (about 2.5% of notes on Meredith) the exact
        # fold decides. The result is identical either way.
        counts = list(self._approx)
        for pc in cur:
            counts[pc] += 1.0
        cov12 = [counts[a] + counts[b] + counts[c] + counts[d] + counts[e] + counts[f] + counts[g]
                 for a, b, c, d, e, f, g in _COVERAGE_PCS]
        best_cov = max(cov12)
        rm = cov12.index(best_cov)
        lim = best_cov - _FRAME_TOL
        for r in range(12):
            if r != rm and cov12[r] > lim:
                return self._frame_centre_exact()
        prev = self._frame_centre
        thr = best_cov - self._frame_margin
        prev_cov = cov12[prev % 12]
        if -_FRAME_TOL <= prev_cov - thr <= _FRAME_TOL:
            return self._frame_centre_exact()
        anchor = self._median(self._committed_lof) if (self._frame_side == "drift" and self._committed_lof) else prev
        fcen = self._fold_center
        frad = self._fold_radius
        best_c, best_side = prev, math.inf
        lo = prev - 12
        c = lo + (rm - lo) % 12
        while c <= prev + 12:
            dc = c - fcen
            sc = abs(c - anchor) + 1000 * max(0, (dc if dc >= 0 else -dc) - frad)
            if sc < best_side:  # increasing c: strict < keeps the earliest on a tie, as the scan does
                best_c = c
                best_side = sc
            c += 12
        dc = prev - fcen
        prev_side = abs(prev - anchor) + 1000 * max(0, (dc if dc >= 0 else -dc) - frad)
        if prev_cov > thr and prev_side <= best_side:
            return prev
        return best_c

    def _frame_centre_exact(self) -> int:
        """The TypeScript computation, reproduced bit for bit (and re-syncs the approximate counts)."""
        cur = self._cur_onset_pcs
        # counts[pc] in TS is a left fold over float additions: 1.0 once per current-onset occurrence, then
        # the decayed weight of every window occurrence, newest first. Floats are not associative, so the
        # fold is reproduced exactly, per pitch class, over that class's occurrence list; the window is fixed
        # within an onset, so each (pc, current-onset count) fold is memoised until the next onset.
        memo = self._frame_memo
        occ = self._occ
        W = self._weights
        top = self._win_seq + 1  # age of window onset `seq` = top - seq (newest = 1)

        def fold(pc: int, k: int) -> float:
            v = 0.0
            for _ in range(k):
                v += 1.0
            for seq in reversed(occ[pc]):
                v += W[top - seq]
            return v

        base = self._frame_base
        if base is None:
            base = self._frame_base = [0.0] * 12
            for pc in range(12):  # fold(pc, 0), inlined: this runs for every pc at every onset
                v = 0.0
                for seq in reversed(occ[pc]):
                    v += W[top - seq]
                base[pc] = v
        self._approx = list(base)
        self._approx_age = 0
        counts = list(base)
        c0: dict[int, int] = {}
        for pc in cur:
            c0[pc] = c0.get(pc, 0) + 1
        for pc, k in c0.items():
            key = k * 12 + pc
            v = memo.get(key)
            if v is None:
                v = memo[key] = fold(pc, k)
            counts[pc] = v

        # coverage(c) depends on c only mod 12: the 7 counts at c-3 .. c+3 summed left to right, as TS does
        # (TS starts from 0.0, and 0.0 + x == x exactly for these non-negative counts).
        cov12 = [counts[a] + counts[b] + counts[c] + counts[d] + counts[e] + counts[f] + counts[g]
                 for a, b, c, d, e, f, g in _COVERAGE_PCS]

        prev = self._frame_centre
        anchor = self._median(self._committed_lof) if (self._frame_side == "drift" and self._committed_lof) else prev
        fcen = self._fold_center
        frad = self._fold_radius

        # TS scans c = prev-12 .. prev+12 keeping (higher coverage, then lower side cost, then earlier c).
        # Every residue occurs in that range, so the winner has the maximum coverage; only those c need a
        # side cost. Same result, same comparisons on the same floats.
        best_cov = max(cov12)
        best_c, best_side = prev, math.inf
        lo = prev - 12
        hi = prev + 12
        for r in range(12):
            if cov12[r] != best_cov:
                continue
            c = lo + (r - lo) % 12
            while c <= hi:
                dc = c - fcen
                sc = abs(c - anchor) + 1000 * max(0, (dc if dc >= 0 else -dc) - frad)
                if sc < best_side or (sc == best_side and c < best_c):  # earliest c wins a tie, as in the scan
                    best_c = c
                    best_side = sc
                c += 12
        dc = prev - fcen
        prev_side = abs(prev - anchor) + 1000 * max(0, (dc if dc >= 0 else -dc) - frad)
        if cov12[prev % 12] >= best_cov - self._frame_margin and prev_side <= best_side:
            return prev
        return best_c

    def _diatonic_anchor(self) -> float:
        w = self._committed_lof
        if not w:
            return self._anchor
        m0 = self._median(w)
        core = [v for v in w if abs(v - m0) <= 3]
        return self._median(core) if core else m0

    @staticmethod
    def _median(v: list[int]) -> int:
        s = sorted(v)
        return s[len(s) // 2]

    def _update_collection(self) -> None:
        w = self._committed_lof
        if not w:
            return
        lo, hi = min(w), max(w)

        def cover(k: int) -> int:
            return sum(1 for v in w if k - 3 <= v <= k + 3)

        cc = self._collection_centre
        best_k, best_cov = cc, cover(cc)
        for k in range(lo + 3, hi + 4):
            cov = cover(k)
            if cov > best_cov or (cov == best_cov and abs(k - cc) < abs(best_k - cc)):
                best_k = k
                best_cov = cov
        if best_cov > cover(cc):
            self._collection_centre = best_k

    # ── read-back / lifecycle ──

    def get_lof(self, midi: int) -> Optional[int]:
        """Line-of-fifths position of :meth:`get_spelling` (parity helper)."""
        n = self._active.get(midi)
        if n is None:
            pc = midi % 12
            n = next((m for m in (self._scale[s] for s in LETTER_ORDER) if pitch_class(m) == pc), None)
        return n

    def get_spelling(self, midi: int) -> Optional[Pitch]:
        n = self.get_lof(midi)
        if n is None:
            return None
        sp = spelling_of(n)
        return Pitch(sp.step, sp.alter, midi // 12 - 1)

    def _keep(self, n: int) -> None:
        last = self._last_by_letter[letter(n)]
        if last is None or last[1] == n:
            self._kept[letter(n)] = n

    def note_off(self, midi: int, t: Optional[float] = None) -> None:
        p = self._pending.get(midi)
        if p is not None:
            del self._pending[midi]
            if t is None or t - p.t >= self._ornament_ms:
                self._keep(p.n)
        c = self._held.get(midi, 1) - 1
        if c > 0:
            self._held[midi] = c
        else:
            self._held.pop(midi, None)
            self._active.pop(midi, None)

    def reset(self, scale: Iterable[PitchClass] = (), hard: bool = False) -> None:
        """Re-seed the frame. Empty clears all state to a cold C-major start; a scale seeds its letters.
        ``hard`` is accepted for API compatibility and ignored, as in TypeScript. ``fold_center`` is kept."""
        self._init_state()
        for pc in scale:
            n = lof_of(pc)
            self._scale[letter(n)] = n

    def set_key(self, key_tonic: int) -> None:
        """Re-centre the spiral clamp-fold basin on a supplied key (major tonic LoF) mid-stream."""
        self._fold_center = key_tonic + 2

    def get_resolved_scale(self) -> list[PitchClass]:
        slots = list(self._scale)
        if self._frame_mode == "diatonic" and self._last_commit is not None:
            slots[letter(self._last_commit)] = self._last_commit
        return [spelling_of(slots[s]) for s in LETTER_ORDER]

    def get_anchor(self) -> int:
        return self._collection_centre

    def get_fold_centre(self) -> float:
        return self._fold_centre

    def get_collection(self) -> list[PitchClass]:
        return collection_at(self._collection_centre)

    def decision(self) -> Optional[Decision]:
        """The most recent note's scoring trace, or None before the first note."""
        if self._last_decision is None:
            return None
        scale, rows, best = self._last_decision
        return Decision(
            scale=[spelling_of(scale[s]) for s in LETTER_ORDER],
            candidates=[DecisionCandidate(spelling_of(r[0]), *r[1:]) for r in rows],
            chosen=spelling_of(best),
        )


def with_onset_tolerance(preset: EngineOptions, onset_tolerance: float) -> EngineOptions:
    return replace(preset, onset_tolerance=onset_tolerance)
