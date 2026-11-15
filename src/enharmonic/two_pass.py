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
from .key import LocalKey, degree_keys, local_keys
from .key_side import anchored_side_keys, centred_side_keys
from .engine import TP_PASS_PRESET, Decision, SpellingEngine, with_onset_tolerance
from .lof import spelling_of
from .pitch import PitchClass
from .resolution import _next_step


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
    #: the aug6 pairs also meet when the two tones' first moves land on one onset (``outward_pairs``'s
    #: ``first_move_meet``: a held or repeated German sixth resolving past the 8-onset window)
    aug6_first_move_meet: bool = True
    #: ``outward_pairs``'s ``chord_meet`` for the passes: a unison meet pairs only if the meeting onset strikes at
    #: least this many notes. Default 0 (off): the look-ahead tier ships it at 3, but on two-pass its fixes were
    #: mostly wrong -> flipped and it did not confirm on the fresh15 set.
    aug6_chord_meet: int = 0
    #: NO PASSAGE BOUNDARY INSIDE A REPEATED RUN. Where the passes disagree, the merge may not place its side change
    #: between two strikes of a repeated run (three or more strikes of one pitch, at most 16 onsets apart, over one
    #: bass pitch class) that lies inside the disagreement: the cut moves to the run's start or end. Confutatis m28:
    #: the bass repeats Eb four times under Cb and Bb; the forward pass reads the bar sharp, the backward pass flat,
    #: and the cut may no longer fall between the strikes (D# x6 then Eb).
    run_boundary: bool = True
    #: NO TRIANGLE, NO SIDE BOUNDARY. A sonority with no major or minor triad in it (a diminished seventh, an augmented
    #: triad, a bare dyad, any other triangle-free set of two or more pitch classes) names its tones only relative to
    #: each other, so it cannot say where a passage changes side. Where the passes disagree over a run made at least
    #: half of such notes, the merge does not place a side boundary by them: each pitch class takes the spelling the
    #: surrounding notes give it (the last merged spelling before the run, the first agreed one after it, within 32
    #: notes each way), and the wolf count decides only among change-points that keep them. If the context before and
    #: after the run spells some pitch class two ways, the rule abstains. WTC II m68: B#, F#, A#, G# in dyads and
    #: triangle-free three-note sets; the rule writes Bach's sharps.
    triangle_free_context: bool = True
    #: an aug6 pair (ten fifths) whose sharper tone rises and flatter tone falls is not a wolf in the merge
    aug6_wedge: bool = True
    #: feed both passes each note's local key (the engine's ``leading_tone_gate``): the ``"tonic-triad"`` table
    #: (a key is its tonic triad inside its seven-note collection; no fitted numbers) on +-12 onsets
    leading_tone_gate: bool = True
    #: feed both passes each note's key-degree key (:func:`enharmonic.degree_keys`: the tonic-triad table on +-12
    #: notes), so the engine's ``key_degree_gate`` keeps a note's letter on a semitone move into a pitch outside
    #: the key
    key_degree_gate: bool = True
    #: KEY -> SIDE. A passage is written on the side of its local key's conventional signature, so both passes count
    #: accidentals from that signature, not from C: each note's key is read offline on +-48 onsets centred on it
    #: (:func:`enharmonic.centred_side_keys`, deepened along the spiral up to seven accidentals), and the passes'
    #: accidental-economy fold counts from its signature. A key with six or seven accidentals (the F#/Gb tie or a
    #: deepened key) is written either way and cannot choose: the pass counts from C there. Clair de lune m44-47
    #: (Db major): the passes keep Debussy's Gb, Db, Eb, Ab instead of F#, C#, D#, G#.
    key_side: bool = True
    #: A KEY KEEPS ITS SIDE ON RETURN (``key_side``'s key reader). A key written on the near side only because its far
    #: reading is past the +-7 deepening limit is a readability respell; when the music returns to the key it came
    #: from, that key keeps its prior side. Pathetique ii: Ab minor (-7) -> Fb major, forced to E major (+4) -> Ab
    #: minor at m48, read -7 again instead of G# minor (+5). Inert when ``key_side`` is off.
    key_return: bool = True
    #: A STRUCK TRIAD SPELLS ITS THIRD MEMBER. After the merge, where the three pitch classes of a major or minor triad
    #: are attacked together at a note's onset and the other two members are spelled coherently with each other (one
    #: spelling each), a note a comma off the triad (a diminished fourth or augmented second inside it) takes the
    #: spelling that completes it, but only toward the local merged centre (median of the +-16 notes around it), and
    #: not when the note is a third that moves a semitone onto the other third of the same root (an appoggiatura:
    #: G#->A over F-C). One shot: nothing it writes feeds another decision. Mozart K. 459/iii, F minor, Ab-C-F struck
    #: together: the merge wrote G#, the rule writes Mozart's Ab.
    struck_triad_third: bool = True
    #: Each input note's NOTATED key signature, in the caller's order: signed sharps - flats (0 = none, -6 = six flats;
    #: the relative major's tonic on the line of fifths), None where unknown. Optional; read by
    #: ``key_signature_anchor``. Must have one entry per note.
    key_signatures: Optional[Sequence[Optional[int]]] = None
    #: THE SIGNATURE ANCHORS THE SIDE (``key_side``, with ``key_signatures``). A passage is notated on the side of its
    #: key signature while its local key stays within one fifth of it: there the passes count accidentals from the
    #: notated signature; farther, they count from the inferred key (:func:`enharmonic.anchored_side_keys`). Chopin
    #: Sonata 2/ii (Eb minor, six flats): the signature settles the F#/Gb tie and m114-179 keep Chopin's Db, Cb, Gb.
    #: Inert without ``key_signatures`` or when ``key_side`` is off.
    key_signature_anchor: bool = True
    #: A SHORT RESPELLED SECTION TAKES THE SURROUNDING SIDE. A composer respells a passage for a section, never for a
    #: few beats: in the merge, a disagreement region of at most 32 onsets and at least 3 pitch classes, whose merged
    #: spelling contradicts the spelling of the same pitch classes just before and just after it (the two contexts
    #: agreeing with each other), takes whole the pass whose spelling the surroundings use, when that pass contradicts
    #: them less than the merge does. Counter-case: a composer's own short respell is undone too (Chopin Barcarolle
    #: m23-24).
    short_flip: bool = True


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


def _resolve_dirs(notes: Sequence[TwoPassNote], horizon: int = 16) -> list[int]:
    """Nearest-semitone-resolution direction (octave-agnostic) within the next ``horizon`` notes."""
    pcs = [x.midi % 12 for x in notes]
    return _next_step(pcs, [(p + 1) % 12 for p in pcs], [(p + 11) % 12 for p in pcs], horizon)


def _resolve_dirs_exact(notes: Sequence[TwoPassNote], horizon: int = 16) -> list[int]:
    """Exact-pitch up/down resolution within the next ``horizon`` notes."""
    midis = [x.midi for x in notes]
    return _next_step(midis, [m + 1 for m in midis], [m - 1 for m in midis], horizon)


#: ``triangle_free_context``: notes read on each side of a disagreement run for the context spellings.
_CONTEXT_NOTES = 32
#: ``short_flip``: the longest region (in onsets) that counts as short (the scorer's flip window is +-16 onsets, so a
#: respell shorter than 32 onsets is never a section), and the fewest pitch classes it must hold.
_SHORT_FLIP_MAX_ONSETS, _SHORT_FLIP_MIN_PCS = 32, 3
#: ``key_side``: the most accidentals a key signature may have and still choose the side.
_KEY_SIDE_MAX_ACCIDENTALS = 5


#: ``struck_triad_third``: notes read on each side for the co-sounding members, the merged centre, and onsets scanned
#: for an appoggiatura's resolution.
_TRIAD_SCAN, _TRIAD_CENTRE_RADIUS, _TRIAD_APP_ONSETS = 64, 16, 8
#: ``struck_triad_third``: major and minor triads as (semitones, fifths) above the root.
_TRIADS = (((0, 0), (4, 4), (7, 1)), ((0, 0), (3, -3), (7, 1)))


def _struck_triad_repairs(N: Sequence[TwoPassNote], out: Sequence[Optional[int]],
                          sounding: Sequence[set[int]]) -> list[tuple[int, int]]:
    """``struck_triad_third``: the (note, spelling) repairs over the merged spellings ``out`` (sorted order)."""
    n = len(N)
    pcs = [x.midi % 12 for x in N]
    fix: list[tuple[int, int]] = []
    for t in range(n):
        o = out[t]
        if o is None:
            continue
        pc, S = pcs[t], sounding[t]
        lo, hi = max(0, t - _TRIAD_SCAN), min(n, t + _TRIAD_SCAN)
        ton, toff = N[t].t_on, N[t].t_off
        # The spellings of each pitch class among the notes overlapping this one (insertion-ordered, like a JS Set).
        co: dict[int, dict[int, None]] = {}
        for k in range(lo, hi):
            if k == t or out[k] is None or not (N[k].t_on < toff and N[k].t_off > ton):
                continue
            co.setdefault(pcs[k], {})[out[k]] = None  # type: ignore[index]

        def struck(q: int) -> bool:
            for k in range(lo, hi):
                if k != t and N[k].t_on == ton and pcs[k] == q:
                    return True
            return False

        want: Optional[int] = None
        clash = False
        for tpl in _TRIADS:
            for ms, mf in tpl:
                r = (pc - ms + 12) % 12
                others = [(s2, f2) for s2, f2 in tpl if s2 != ms]
                if not all((r + s2) % 12 in S and len(co.get((r + s2) % 12, ())) == 1 for s2, _ in others):
                    continue
                if not all(struck((r + s2) % 12) for s2, _ in others):
                    continue
                # An appoggiatura: a third moving a semitone onto the OTHER third of the same root (G#->A over F-C).
                if ms == 3 or ms == 4:
                    alt = (r + (4 if ms == 3 else 3)) % 12
                    dm = 1 if ms == 3 else -1
                    seen = 0
                    last_on = ton
                    hit = False
                    k = t + 1
                    while k < n and seen < _TRIAD_APP_ONSETS:
                        if N[k].t_on != last_on:
                            seen += 1
                            last_on = N[k].t_on
                        if N[k].t_on > ton and N[k].midi == N[t].midi + dm and pcs[k] == alt:
                            hit = True
                            break
                        k += 1
                    if hit:
                        continue
                roots = [next(iter(co[(r + s2) % 12])) - f2 for s2, f2 in others]
                if roots[0] != roots[1]:  # the other two members are not coherent
                    continue
                w = roots[0] + mf
                if want is not None and want != w:
                    clash = True
                else:
                    want = w
        if clash or want is None or want == o or abs(want - o) != 12:
            continue
        # Only toward the local merged centre.
        loc = sorted(out[k] for k in range(max(0, t - _TRIAD_CENTRE_RADIUS), min(n - 1, t + _TRIAD_CENTRE_RADIUS) + 1)
                     if k != t and out[k] is not None)  # type: ignore[type-var]
        cen = loc[len(loc) >> 1] if loc else 0
        if abs(want - cen) >= abs(o - cen):
            continue
        fix.append((t, want))
    return fix


def _side_signatures(notes: Sequence[TwoPassNote], key_return: bool) -> list[Optional[float]]:
    """``key_side``: each note's signature for the passes (0 = count from C), None before any key is read."""
    return [None if x is None else 0 if (math.isnan(x) or abs(x) > _KEY_SIDE_MAX_ACCIDENTALS) else x
            for x in centred_side_keys(notes, key_return=key_return)]


def _pass_signatures(notes: Sequence[TwoPassNote], opts: TwoPassOptions,
                     notated: Optional[Sequence[Optional[int]]]) -> list[Optional[float]]:
    """``key_side``: the per-note signatures for the passes (sorted order). With ``key_signature_anchor`` and notated
    signatures, the notated signature where the local key is within one fifth of it, else the inferred one."""
    inferred = _side_signatures(notes, opts.key_return)
    return anchored_side_keys(notes, notated, inferred) if notated is not None and opts.key_signature_anchor else inferred


def _notated_signatures(idx: list[int], n: int, opts: TwoPassOptions) -> Optional[list[Optional[int]]]:
    """``key_signatures`` in sorted order (None when not given)."""
    ks = opts.key_signatures
    if ks is None:
        return None
    if len(ks) != n:
        raise ValueError(f"key_signatures has {len(ks)} entries for {n} notes")
    return [ks[i] for i in idx]


def _drive_pass(notes: Sequence[TwoPassNote], dirs: Optional[list[int]], exact: Optional[list[int]],
                onset_tolerance: float = 0, frames: Optional[list[Optional[TwoPassFrame]]] = None,
                outward: Optional[tuple[list[Optional[int]], list[int]]] = None,
                keys: Optional[Sequence[Optional[LocalKey]]] = None,
                deg_keys: Optional[Sequence[Optional[LocalKey]]] = None,
                signatures: Optional[Sequence[Optional[float]]] = None) -> list[Optional[int]]:
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
            if signatures is not None:
                sig = signatures[i]
                if sig is not None:
                    s.set_signature(sig)
            s.note_on(midi, t, dirs[i] if dirs is not None else 0, exact[i] if exact is not None else 0,
                      outward[0][i] if outward is not None else None, outward[1][i] if outward is not None else 0,
                      keys[i] if keys is not None else None, deg_keys[i] if deg_keys is not None else None)
            pend.setdefault(midi, []).append(i)
            if frames is not None:
                frames[i] = TwoPassFrame(s.get_resolved_scale(), s.get_fold_centre(), s.decision())
        else:
            q = pend.get(midi)
            if q:
                out[q.pop(0)] = s.get_lof(midi)
            s.note_off(midi, t)
    return out


def _run_pairs(N: Sequence[TwoPassNote]) -> list[tuple[int, int]]:
    """Repeated runs, for the merge's no-boundary rule: three or more strikes of one MIDI pitch, consecutive strikes
    at most 16 onsets apart, over one bass pitch class (the lowest note sounding at each strike). Returns every pair
    of consecutive strikes (a, b) (indices into the sorted notes), sorted. A re-struck pitch is read as one voice."""
    n = len(N)
    onset_idx: list[int] = []
    g = -1
    last_t = -math.inf
    for x in N:
        if x.t_on != last_t:
            g += 1
            last_t = x.t_on
        onset_idx.append(g)
    bass: list[int] = []
    for k, x in enumerate(N):
        b = x.midi
        for m in range(k - 1, max(0, k - 64) - 1, -1):
            if N[m].t_off > x.t_on and N[m].midi < b:
                b = N[m].midi
        m = k + 1
        while m < min(n, k + 64) and N[m].t_on == x.t_on:
            if N[m].midi < b:
                b = N[m].midi
            m += 1
        bass.append(b % 12)
    by_midi: dict[int, list[int]] = {}
    for k, x in enumerate(N):
        a = by_midi.setdefault(x.midi, [])
        if not a or N[a[-1]].t_on != x.t_on:
            a.append(k)
    pairs: list[tuple[int, int]] = []
    for ks in by_midi.values():
        cur: list[int] = []
        for k in ks:
            if cur:
                p = cur[-1]
                if onset_idx[k] - onset_idx[p] > 16 or bass[k] != bass[p]:
                    if len(cur) >= 3:
                        pairs.extend(zip(cur, cur[1:]))
                    cur = []
            cur.append(k)
        if len(cur) >= 3:
            pairs.extend(zip(cur, cur[1:]))
    pairs.sort()
    return pairs


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


def _two_pass_core(N: Sequence[TwoPassNote], opts: TwoPassOptions, trace_frames: bool = False,
                   notated: Optional[Sequence[Optional[int]]] = None) -> _CoreResult:
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
        op = outward_pairs(N, first_move_meet=opts.aug6_first_move_meet, chord_meet=opts.aug6_chord_meet)
        out_f = ([N[y].midi if y >= 0 else None for y in op.partner], op.role)
    # The local keys are read in forward time on onsets centred on each note; the backward pass reads them too.
    keys_f = local_keys(N, "tonic-triad", 12) if opts.leading_tone_gate else None
    deg_f = degree_keys(N) if opts.key_degree_gate else None
    sig_f = _pass_signatures(N, opts, notated) if opts.key_side else None
    fwd = _drive_pass(N, dirs_f, exact_f, opts.onset_tolerance, f_frames, out_f, keys_f, deg_f, sig_f)
    T = max(x.t_off for x in N)
    b_notes = [TwoPassNote(x.midi, T - x.t_off, T - x.t_on) for x in N][::-1]
    b_dirs = None if backward_la == "off" else dirs_f[::-1]
    b_out = _drive_pass(b_notes, b_dirs if backward_la != "off" else None,
                        exact_f[::-1] if backward_la != "off" else None, opts.onset_tolerance, b_frames,
                        (out_f[0][::-1], out_f[1][::-1]) if out_f is not None else None,
                        keys_f[::-1] if keys_f is not None else None,
                        deg_f[::-1] if deg_f is not None else None,
                        sig_f[::-1] if sig_f is not None else None)
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

    # NO TRIANGLE, NO SIDE BOUNDARY (see triangle_free_context). A note is triangle-free when the pitch classes
    # sounding during it hold no major or minor triad and it is not a lone pitch class (a single pc inside a dim7 pair
    # still counts: the dim7 is the sonority).
    def has_triad(st: set[int]) -> bool:
        for r in st:
            if (r + 7) % 12 in st and ((r + 4) % 12 in st or (r + 3) % 12 in st):
                return True
        return False

    def dim7_note(t: int) -> bool:
        return (t > 0 and in_dim7(t - 1, t)) or (t + 1 < n and in_dim7(t, t + 1))

    tri_free: Optional[list[bool]] = (
        [not has_triad(sounding[t]) and (len(sounding[t]) >= 2 or dim7_note(t)) for t in range(n)]
        if opts.triangle_free_context else None)
    # A SHORT RESPELLED SECTION TAKES THE SURROUNDING SIDE (see short_flip). Reads the context before [i, j) in the
    # merge and after it in the agreeing notes (the first spelling of each pitch class within _CONTEXT_NOTES notes);
    # if no pitch class is spelled differently before and after, and the region is short and holds enough pitch
    # classes, the pass that contradicts the context less replaces the merge when the merge contradicts it more.
    def short_flip_repair(i: int, j: int) -> None:
        before: dict[int, int] = {}
        t, c = i - 1, 0
        while t >= 0 and c < _CONTEXT_NOTES:
            if out[t] is not None:
                c += 1
                before.setdefault(pcs_n[t], out[t])  # type: ignore[arg-type]
            t -= 1
        after: dict[int, Optional[int]] = {}
        t, c = j, 0
        while t < n and c < _CONTEXT_NOTES:
            if agree_arr[t]:
                c += 1
                if pcs_n[t] not in after:
                    after[pcs_n[t]] = fwd[t]
            t += 1
        for q, v in after.items():
            b = before.get(q)
            if b is not None and b != v:
                return
        onsets = 0
        pcs_r: set[int] = set()
        for t in range(i, j):
            pcs_r.add(pcs_n[t])
            if t == i or N[t].t_on != N[t - 1].t_on:
                onsets += 1
        if onsets > _SHORT_FLIP_MAX_ONSETS or len(pcs_r) < _SHORT_FLIP_MIN_PCS:
            return

        def conflicts(sp: Sequence[Optional[int]]) -> int:
            c = 0
            for t in range(i, j):
                v = sp[t]
                b = before.get(pcs_n[t])
                a = after.get(pcs_n[t])
                if b is not None and b != v:
                    c += 1
                if a is not None and a != v:
                    c += 1
            return c

        c_f, c_b, c_o = conflicts(fwd), conflicts(bwd), conflicts(out)
        pas = fwd if c_f <= c_b else bwd
        if c_o > min(c_f, c_b):
            for t in range(i, j):
                out[t] = pas[t]

    runs = _run_pairs(N) if opts.run_boundary else []
    run_at = 0  # runs[] sorted by first strike; disagreement runs only move forward
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

        # NO BOUNDARY INSIDE A REPEATED RUN: the run pairs with both strikes in [i, j). A change-point that splits
        # one costs more than any wolf count in the window, so the cut lands before or after the run.
        while run_at < len(runs) and runs[run_at][0] < i:
            run_at += 1
        inner: list[tuple[int, int]] = []
        r = run_at
        while r < len(runs) and runs[r][0] < j:
            if runs[r][1] < j:
                inner.append(runs[r])
            r += 1
        W = j - i + 2
        cost: list[int] = []
        min_cost = math.inf
        for k in range(i, j + 1):
            c = cost_of(k)
            if inner:
                c += W * sum(1 for a, b in inner if pick(k, a) != pick(k, b))
            cost.append(c)
            if c < min_cost:
                min_cost = c
        # A run made mostly of triangle-free notes: each context spelling (before, after) that a change-point
        # contradicts costs more than any wolf count.
        if tri_free is not None and sum(1 for t in range(i, j) if tri_free[t]) >= (j - i) / 2:
            ctx: dict[int, list[Optional[int]]] = {}
            t, c = i - 1, 0
            while t >= 0 and c < _CONTEXT_NOTES:
                if out[t] is not None:
                    c += 1
                    ctx.setdefault(pcs_n[t], [out[t]])
                t -= 1
            after: dict[int, Optional[int]] = {}
            t, c = j, 0
            while t < n and c < _CONTEXT_NOTES:
                if agree_arr[t]:
                    c += 1
                    if pcs_n[t] not in after:
                        after[pcs_n[t]] = fwd[t]
                t += 1
            split = False
            for q, v in after.items():
                a = ctx.get(q)
                if a is not None:
                    if a[0] != v:
                        split = True
                    a.append(v)
                else:
                    ctx[q] = [v]
            if split:
                ctx.clear()
            if ctx:
                min_cost = math.inf
                for k in range(i, j + 1):
                    conflicts = 0
                    for t in range(i, j):
                        pk = pick(k, t)
                        for v in ctx.get(pcs_n[t], ()):
                            if v != pk:
                                conflicts += 1
                    cost[k - i] = cost[k - i] + conflicts * 1e6
                    if cost[k - i] < min_cost:
                        min_cost = cost[k - i]
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
        if opts.short_flip:
            short_flip_repair(i, j)
        i = j

    # A STRUCK TRIAD SPELLS ITS THIRD MEMBER (see struck_triad_third); all repairs read the merge, then apply.
    if opts.struck_triad_third:
        for t, w in _struck_triad_repairs(N, out, sounding):
            out[t] = w

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
    core = _two_pass_core([ns[i] for i in idx], opts, False, _notated_signatures(idx, len(ns), opts))
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
    core = _two_pass_core([ns[i] for i in idx], opts, True, _notated_signatures(idx, len(ns), opts))
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
