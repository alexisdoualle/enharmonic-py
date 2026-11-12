"""Two-pass batch speller: the offline, highest-accuracy entry point (port of ``src/two-pass.ts``).

Run the engine FORWARD and again over the time-REVERSED note stream, then reconcile: keep agreements,
trust the warm pass at each cold end, and place one change-point per disagreement run by local wolf-cost,
breaking ties toward the piece's committed line-of-fifths centre.

JavaScript ``undefined`` (a note never read back, e.g. a zero-length note whose release sorts before its
strike) is ``None`` here, and the arithmetic it poisons in TypeScript (``undefined - x`` is ``NaN``) is
reproduced with NaN so the merge takes the same branches.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Literal, NamedTuple, Optional, Sequence

from .aug6 import outward_pairs
from .key import LocalKey, local_keys
from .engine import TP_PASS_PRESET, Decision, SpellingEngine, with_onset_tolerance
from .lof import spelling_of
from .pitch import PitchClass


class TwoPassNote(NamedTuple):
    midi: int
    t_on: float
    t_off: float


@dataclass(frozen=True)
class TwoPassOptions:
    backward_look_ahead: Literal["forward", "off"] = "forward"
    centre_tiebreak: bool = True
    centre_margin: float = 0
    honor_resolution: bool = False
    onset_tolerance: float = 0
    #: feed both passes the aug6 outward pairs (the engine's ``aug6_outward`` rule)
    aug6_outward: bool = True
    #: an aug6 pair (ten fifths) whose sharper tone rises and flatter tone falls is not a wolf in the merge
    aug6_wedge: bool = True
    #: feed both passes each note's local key (the engine's ``leading_tone_gate``): the ``"tonic-triad"`` table
    #: (a key is its tonic triad inside its seven-note collection; no fitted numbers) on +-12 onsets
    leading_tone_gate: bool = True


@dataclass(frozen=True)
class TwoPassFrame:
    surface: list[PitchClass]
    fold_centre: float
    decision: Optional[Decision]


@dataclass(frozen=True)
class TwoPassNoteTrace:
    forward: Optional[PitchClass]
    backward: Optional[PitchClass]
    selected: Optional[PitchClass]
    agrees: bool
    source: Literal["forward", "backward"]
    frame: Optional[TwoPassFrame]


@dataclass(frozen=True)
class TwoPassTrace:
    spellings: list[Optional[PitchClass]]
    notes: list[TwoPassNoteTrace]
    first_stable: Optional[int]
    last_stable: Optional[int]


def _as_note(x: Any) -> TwoPassNote:
    if isinstance(x, TwoPassNote):
        return x
    if isinstance(x, dict):
        return TwoPassNote(x["midi"], x.get("t_on", x.get("tOn")), x.get("t_off", x.get("tOff")))
    if isinstance(x, (tuple, list)):
        return TwoPassNote(x[0], x[1], x[2])
    return TwoPassNote(x.midi, x.t_on, x.t_off)


def _num(v: Optional[float]) -> float:
    """JS numeric coercion of a maybe-undefined value: undefined -> NaN."""
    return math.nan if v is None else v


# ── Direction helpers ─────────────────────────────────────────────────────────


def _next_step(keys: list[int], up: list[int], down: list[int], horizon: int) -> list[int]:
    """For each a, +1 if the first b in (a, a + horizon] with keys[b] == up[a] or down[a] matches ``up``,
    -1 if it matches ``down``, 0 if there is none: the forward scan of the TS ``resolveDirs``, computed in
    one backward sweep with the next index of every key (the scan's first hit is the nearer of the two)."""
    n = len(keys)
    d = [0] * n
    nxt: dict[int, int] = {}
    for a in range(n - 1, -1, -1):
        bu = nxt.get(up[a], n)
        bd = nxt.get(down[a], n)
        b = bu if bu < bd else bd
        if b < n and b - a <= horizon:
            d[a] = 1 if b == bu else -1
        nxt[keys[a]] = a
    return d


def _resolve_dirs(notes: Sequence[TwoPassNote], horizon: int = 16) -> list[int]:
    """Nearest-semitone-resolution direction (octave-agnostic) within the next ``horizon`` notes."""
    pcs = [x.midi % 12 for x in notes]
    return _next_step(pcs, [(p + 1) % 12 for p in pcs], [(p + 11) % 12 for p in pcs], horizon)


def _resolve_dirs_exact(notes: Sequence[TwoPassNote], horizon: int = 16) -> list[int]:
    """Exact-pitch up/down resolution within the next ``horizon`` notes."""
    midis = [x.midi for x in notes]
    return _next_step(midis, [m + 1 for m in midis], [m - 1 for m in midis], horizon)


def _drive_pass(notes: Sequence[TwoPassNote], dirs: Optional[list[int]], exact: Optional[list[int]],
                onset_tolerance: float = 0, frames: Optional[list[Optional[TwoPassFrame]]] = None,
                outward: Optional[tuple[list[Optional[int]], list[int]]] = None,
                keys: Optional[Sequence[Optional[LocalKey]]] = None) -> list[Optional[int]]:
    s = SpellingEngine(with_onset_tolerance(TP_PASS_PRESET, onset_tolerance) if onset_tolerance else TP_PASS_PRESET)
    # Releases before strikes at equal t; strikes bass-first. Stable, like Array.prototype.sort.
    evs: list[tuple[float, bool, int]] = []
    for i, x in enumerate(notes):
        evs.append((x.t_on, True, i))
        evs.append((x.t_off, False, i))
    evs.sort(key=lambda e: (e[0], e[1], notes[e[2]].midi))
    out: list[Optional[int]] = [None] * len(notes)
    pend: dict[int, list[int]] = {}
    for t, on, i in evs:
        midi = notes[i].midi
        if on:
            s.note_on(midi, t, dirs[i] if dirs is not None else 0, exact[i] if exact is not None else 0,
                      outward[0][i] if outward is not None else None, outward[1][i] if outward is not None else 0,
                      keys[i] if keys is not None else None)
            pend.setdefault(midi, []).append(i)
            if frames is not None:
                frames[i] = TwoPassFrame(s.get_resolved_scale(), s.get_fold_centre(), s.decision())
        else:
            q = pend.get(midi)
            if q:
                out[q.pop(0)] = s.get_lof(midi)
            s.note_off(midi, t)
    return out


def _order(notes: Sequence[TwoPassNote]) -> list[int]:
    return sorted(range(len(notes)), key=lambda i: (notes[i].t_on, notes[i].midi))


def _restore(idx: list[int], spelled: list[Any]) -> list[Any]:
    out: list[Any] = [None] * len(idx)
    for pos, orig in enumerate(idx):
        out[orig] = spelled[pos]
    return out


class _CoreResult(NamedTuple):
    spellings: list[Optional[int]]
    forward: list[Optional[int]]
    backward: list[Optional[int]]
    agrees: list[bool]
    forward_frames: Optional[list[Optional[TwoPassFrame]]]
    backward_frames: Optional[list[Optional[TwoPassFrame]]]
    first_stable: int
    last_stable: int


def _two_pass_core(N: Sequence[TwoPassNote], opts: TwoPassOptions, trace_frames: bool = False) -> _CoreResult:
    backward_la = opts.backward_look_ahead
    centre_tiebreak = opts.centre_tiebreak
    centre_margin = opts.centre_margin
    wedge = opts.aug6_wedge
    n = len(N)
    if n == 0:
        return _CoreResult([], [], [], [], None, None, 0, -1)

    dirs_f = _resolve_dirs(N)
    exact_f = _resolve_dirs_exact(N)
    f_frames: Optional[list[Optional[TwoPassFrame]]] = [None] * n if trace_frames else None
    b_frames: Optional[list[Optional[TwoPassFrame]]] = [None] * n if trace_frames else None
    out_f: Optional[tuple[list[Optional[int]], list[int]]] = None
    if opts.aug6_outward:
        op = outward_pairs(N)
        out_f = ([N[y].midi if y >= 0 else None for y in op.partner], op.role)
    # The local keys are read in forward time on onsets centred on each note; the backward pass reads them too.
    keys_f = local_keys(N, "tonic-triad", 12) if opts.leading_tone_gate else None
    fwd = _drive_pass(N, dirs_f, exact_f, opts.onset_tolerance, f_frames, out_f, keys_f)
    T = max(x.t_off for x in N)
    b_notes = [TwoPassNote(x.midi, T - x.t_off, T - x.t_on) for x in N][::-1]
    b_dirs = None if backward_la == "off" else dirs_f[::-1]
    b_out = _drive_pass(b_notes, b_dirs if backward_la != "off" else None,
                        exact_f[::-1] if backward_la != "off" else None, opts.onset_tolerance, b_frames,
                        (out_f[0][::-1], out_f[1][::-1]) if out_f is not None else None,
                        keys_f[::-1] if keys_f is not None else None)
    bwd: list[Optional[int]] = [None] * n
    for k in range(n):
        bwd[n - 1 - k] = b_out[k]
    if b_frames is not None:
        b_frames.reverse()

    out: list[Optional[int]] = [None] * n
    agree_arr = [fwd[i] == bwd[i] for i in range(n)]  # undefined === undefined is true in JS too

    first_agree = 0
    while first_agree < n and not agree_arr[first_agree]:
        first_agree += 1
    last_agree = n - 1
    while last_agree >= 0 and not agree_arr[last_agree]:
        last_agree -= 1
    for i in range(first_agree):
        out[i] = bwd[i]
    for i in range(last_agree + 1, n):
        out[i] = fwd[i]

    # Median LoF of the agreed notes. JS sort puts undefined last without calling the comparator.
    agreed = [fwd[t] for t in range(n) if agree_arr[t]]
    defined = sorted(v for v in agreed if v is not None)
    agreed_lofs: list[Optional[int]] = list(defined) + [None] * (len(agreed) - len(defined))
    centre: float = _num(agreed_lofs[len(agreed_lofs) >> 1]) if agreed_lofs else 0

    # Sounding pitch classes during each note (for the dim7 skip).
    # (Only membership and all-of tests read these sets, so the order notes are added in does not matter.)
    pcs_n = [x.midi % 12 for x in N]
    ton = [x.t_on for x in N]
    toff = [x.t_off for x in N]
    sounding: list[set[int]] = []
    for k in range(n):
        tk_on, tk_off = ton[k], toff[k]
        s = {pcs_n[m] for m in range(max(0, k - 64), k) if toff[m] > tk_on}
        s.add(pcs_n[k])
        m = k + 1
        lim = min(n, k + 64)
        while m < lim and ton[m] < tk_off:
            s.add(pcs_n[m])
            m += 1
        sounding.append(s)

    def in_dim7(a: int, b: int) -> bool:
        pa, pb = N[a].midi % 12, N[b].midi % 12
        if pa == pb or (pb - pa + 12) % 3 != 0:
            return False
        sa, sb = sounding[a], sounding[b]
        held = sum(1 for d in (3, 6, 9) if ((pa + d) % 12) in sa or ((pa + d) % 12) in sb)
        if held == 3:
            return True
        if held < 2:
            return False
        for q in sa:
            if (q - pa + 12) % 3 != 0:
                return False
        for q in sb:
            if (q - pa + 12) % 3 != 0:
                return False
        return True

    i = max(0, first_agree)
    while i <= last_agree:
        if agree_arr[i]:
            out[i] = fwd[i]
            i += 1
            continue
        j = i
        while j <= last_agree and not agree_arr[j]:
            j += 1
        late = (i + j) / 2 >= n / 2

        def pick(k: int, t: int, i: int = i, j: int = j) -> Optional[int]:
            if t < i:
                return out[t]
            if t >= j:
                return fwd[t]
            return fwd[t] if t < k else bwd[t]

        def cost_of(k: int, i: int = i, j: int = j) -> int:
            cost = 0
            for t in range(max(1, i), min(n - 1, j) + 1):
                if in_dim7(t - 1, t):
                    continue
                a, b = pick(k, t - 1), pick(k, t)
                if a is not None and b is not None and abs(a - b) >= 6:
                    # The aug6 WEDGE: ten fifths, the sharper tone rising and the flatter falling a semitone.
                    if wedge and abs(a - b) == 10:
                        hi, lo = (t - 1, t) if a > b else (t, t - 1)
                        if exact_f[hi] == 1 and exact_f[lo] == -1:
                            continue
                    cost += 1
            return cost

        cost: list[int] = []
        min_cost = math.inf
        for k in range(i, j + 1):
            c = cost_of(k)
            cost.append(c)
            if c < min_cost:
                min_cost = c
        best_k, best_pref, best_warm = i, -math.inf, -math.inf
        for k in range(i, j + 1):
            ck = cost[k - i]
            if ck > min_cost + centre_margin:
                continue
            dist = 0.0
            for t in range(i, j):
                dist += abs(_num(pick(k, t)) - centre)
            pref = -(dist + ck) if centre_tiebreak else -ck
            warm = k if late else -k
            if pref > best_pref or (pref == best_pref and warm > best_warm):
                best_k, best_pref, best_warm = k, pref, warm
        for t in range(i, j):
            out[t] = fwd[t] if t < best_k else bwd[t]
        i = j

    if opts.honor_resolution:
        dirs_x = _resolve_dirs_exact(N)

        def pc(k: int) -> int:
            return N[k].midi % 12

        same_onset: dict[float, list[int]] = {}
        for idx in range(n):
            same_onset.setdefault(N[idx].t_on, []).append(idx)
        onset_pcs = {t: {pc(k) for k in idxs} for t, idxs in same_onset.items()}

        def in_full_dim7(k: int) -> bool:
            pcs = onset_pcs[N[k].t_on]
            L = pc(k)
            return L in pcs and (L + 3) % 12 in pcs and (L + 6) % 12 in pcs and (L + 9) % 12 in pcs

        def v_wolf(cand: int, k: int) -> int:
            w = 0
            for jj in same_onset[N[k].t_on]:
                p = out[jj]
                if jj == k or p is None:
                    continue
                if pc(jj) == pc(k):
                    continue
                if abs(cand - p) >= 7:
                    w += 1
            return w

        for idx in range(n):
            cur, f = out[idx], fwd[idx]
            if (dirs_x[idx] == 1 and cur is not None and f is not None and f > cur
                    and (v_wolf(f, idx) <= v_wolf(cur, idx) or in_full_dim7(idx))):
                out[idx] = f

    return _CoreResult(out, fwd, bwd, agree_arr, f_frames, b_frames,
                       first_agree if first_agree < n else n, last_agree)


def _to_pc(n: Optional[int]) -> Optional[PitchClass]:
    return None if n is None else spelling_of(n)


def spell_two_pass_lof(notes: Iterable[Any], opts: TwoPassOptions = TwoPassOptions()) -> list[Optional[int]]:
    """:func:`spell_two_pass` as line-of-fifths positions (None where TypeScript returns null)."""
    ns = [_as_note(x) for x in notes]
    if not ns:
        return []
    idx = _order(ns)
    core = _two_pass_core([ns[i] for i in idx], opts)
    return _restore(idx, core.spellings)


def spell_two_pass(notes: Iterable[Any], opts: TwoPassOptions = TwoPassOptions()) -> list[Optional[PitchClass]]:
    """Production entry point: one spelling per input note, in the caller's order.

    ``notes`` items may be :class:`TwoPassNote`, ``(midi, t_on, t_off)`` tuples, or dicts with
    ``midi``/``t_on``/``t_off`` (or the TypeScript ``tOn``/``tOff`` keys).
    """
    return [_to_pc(n) for n in spell_two_pass_lof(notes, opts)]


def spell_two_pass_traced(notes: Iterable[Any], opts: TwoPassOptions = TwoPassOptions()) -> TwoPassTrace:
    """Diagnostic entry point: the production resolver plus each note's forward/backward/selected trace."""
    ns = [_as_note(x) for x in notes]
    if not ns:
        return TwoPassTrace([], [], None, None)
    idx = _order(ns)
    core = _two_pass_core([ns[i] for i in idx], opts, True)
    spellings = _restore(idx, [_to_pc(n) for n in core.spellings])
    traces: list[TwoPassNoteTrace] = []
    for k, sp in enumerate(core.spellings):
        source: Literal["forward", "backward"] = "forward" if sp == core.forward[k] else "backward"
        frames = core.forward_frames if source == "forward" else core.backward_frames
        traces.append(TwoPassNoteTrace(
            _to_pc(core.forward[k]), _to_pc(core.backward[k]), _to_pc(sp), core.agrees[k], source,
            frames[k] if frames is not None else None,
        ))
    return TwoPassTrace(
        spellings, _restore(idx, traces),
        core.first_stable if core.first_stable < len(core.spellings) else None,
        core.last_stable if core.last_stable >= 0 else None,
    )
