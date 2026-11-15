"""Augmented sixths by outward convergence (port of ``src/aug6.ts``).

Proposition: two tones ten semitones apart that move outward and meet on one pitch class are an augmented
sixth, and the rising tone is the #4 (Ab-C-Eb-F# -> G: F# rises and Ab falls onto G). Its spelling is ten
fifths sharp of its partner's, never the dominant seventh's minor seventh (Gb).

:func:`outward_pairs` finds the pairs from the notes alone (it needs the future, so it is look-ahead or offline
context, like ``resolve_dir``). The engine's ``aug6_outward`` option applies them behind a key gate.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Sequence

#: How many onsets after a note the onset finder looks for its first semitone move.
_MOVE_ONSETS = 64
#: How far (in notes) on either side of a note to look for notes still sounding at its onset.
_SOUNDING_NOTES = 64


class OutwardPairs(NamedTuple):
    """Per note: the partner's index (-1 none) and its role (+1 rising upper tone, -1 falling lower tone)."""

    partner: list[int]
    role: list[int]


def _first_exact_moves(notes: Sequence[Any], at: list[int]) -> list[int]:
    """Each note's FIRST semitone move at the exact pitch, read by onset: skip later onsets that strike the
    note's pitch class again, then take the first onset holding a semitone neighbour in any octave; +1 if it
    has the exact semitone above (and not below), -1 for below, else 0. Notes sorted by onset. ``at``
    receives, per note, the index of the first note of the onset its move lands on (-1: no move)."""
    n = len(notes)
    exact = [0] * n
    midis = [x.midi for x in notes]
    cluster_of = [0] * n
    c_start: list[int] = []
    last_t: Any = None
    for i, x in enumerate(notes):
        if not c_start or x.t_on != last_t:
            c_start.append(i)
            last_t = x.t_on
        cluster_of[i] = len(c_start) - 1
    C = len(c_start)
    c_end = c_start[1:] + [n]
    struck = [0] * C
    for c in range(C):
        for i in range(c_start[c], c_end[c]):
            struck[c] |= 1 << (midis[i] % 12)
    for a in range(n):
        m = midis[a]
        c0 = cluster_of[a]
        bit = 1 << (m % 12)
        near = (1 << ((m + 1) % 12)) | (1 << ((m - 1) % 12))
        g = c0 + 1
        lim = min(C, c0 + _MOVE_ONSETS + 1)
        while g < lim:
            sg = struck[g]
            # A re-strike of the pitch class, or no semitone neighbour at all: not this onset.
            if sg & bit or not sg & near:
                g += 1
                continue
            best = None
            bd = 0
            conflict = False
            up = dn = False
            for i in range(c_start[g], c_end[g]):
                o = midis[i]
                pd = (o - m) % 12
                if pd != 1 and pd != 11:
                    continue
                d = 1 if pd == 1 else -1
                dist = abs(o - m)
                if o == m + 1:
                    up = True
                if o == m - 1:
                    dn = True
                if best is None or dist < best:
                    best, bd, conflict = dist, d, False
                elif dist == best and d != bd:
                    conflict = True
            x = 1 if (up and not dn) else -1 if (dn and not up) else 0
            if (0 if conflict else bd) == 0 and x == 0:
                g += 1
                continue
            exact[a] = x
            at[a] = c_start[g]
            break
    return exact


def outward_pairs(notes: Sequence[Any], look: int = 8, first_move_meet: bool = True, chord_meet: int = 3) -> OutwardPairs:
    """Outward-convergence pairs. Note x and a note y sounding at x's onset pair up when x is ten semitones
    above y by pitch class, the major third above y sounds, each tone's first move is outward (x up, y down),
    and one onset among the next ``look`` after x's strikes both x+1 and y-1 at the exact pitch, or
    (``first_move_meet``) both first moves land on one onset. ``notes`` have ``midi``, ``t_on``, ``t_off``
    and are sorted by onset.

    MEETING BY FIRST MOVES (``first_move_meet``, default True): the two tones of an augmented sixth also meet
    when their first moves (re-strikes skipped) land on one onset, however far away: a German sixth repeated
    across many onsets (Haydn Sym. 99/ii) resolves past the look window, but both tones still move at once.

    A UNISON MEET RESOLVES INTO A CHORD (``chord_meet``, default 3, the look-ahead tier's; 0 turns it off): when the
    two goals are one pitch (the tones a major second apart closing onto a unison), the pair counts only if the
    meeting onset strikes at least this many notes. Two lone voices crossing on one note are passing, not an
    augmented sixth: in C minor, Ab and F# landing on a struck G chord are an aug6; the same two tones meeting on a
    bare G are not. :func:`enharmonic.spell_two_pass` passes 0 unless ``aug6_chord_meet`` is set."""
    n = len(notes)
    partner = [-1] * n
    role = [0] * n
    move_at = [-1] * n
    moves = _first_exact_moves(notes, move_at)
    midis = [x.midi for x in notes]
    pcs = [m % 12 for m in midis]
    ton = [x.t_on for x in notes]
    toff = [x.t_off for x in notes]
    # End (exclusive) of each note's onset: notes after it start later, so they never sound at its onset.
    onset_end = [0] * n
    e = n
    for i in range(n - 1, -1, -1):
        if i + 1 < n and ton[i + 1] != ton[i]:
            e = i + 1
        onset_end[i] = e
    for x in range(n):
        # (TS tests these per partner; checking the cheap conditions first visits the same partners in the
        # same index order.)
        if moves[x] != 1:
            continue
        t = ton[x]
        lo_k, hi_k = max(0, x - _SOUNDING_NOTES), min(n, x + _SOUNDING_NOTES, onset_end[x])
        want = (pcs[x] - 10) % 12
        cands = [k for k in range(lo_k, hi_k) if pcs[k] == want and moves[k] == -1 and ton[k] <= t and toff[k] > t]
        if not cands:
            continue
        sounding_pcs = {pcs[k] for k in range(lo_k, hi_k) if ton[k] <= t and toff[k] > t}
        for y in cands:
            if (pcs[y] + 4) % 12 not in sounding_pcs:
                continue
            g = x
            while g < n and ton[g] == t:
                g += 1
            # A unison meet (both goals one pitch) needs a chord at the meeting onset (chord_meet); onset_end[h] - h
            # is how many notes the onset starting at note h strikes.
            unison = chord_meet > 0 and midis[x] + 1 == midis[y] - 1
            met = False
            seen = 0
            while g < n and seen < look:
                tg = ton[g]
                g0 = g
                up = dn = False
                while g < n and ton[g] == tg:
                    if midis[g] == midis[x] + 1:
                        up = True
                    if midis[g] == midis[y] - 1:
                        dn = True
                    g += 1
                if up and dn and not (unison and onset_end[g0] - g0 < chord_meet):
                    met = True
                    break
                seen += 1
            # "The two tones meet when their first moves, re-strikes skipped, land on one onset."
            if (not met and first_move_meet and move_at[x] >= 0 and move_at[x] == move_at[y]
                    and not (unison and onset_end[move_at[x]] - move_at[x] < chord_meet)):
                met = True
            if not met:
                continue
            if partner[x] < 0:
                partner[x], role[x] = y, 1
            if partner[y] < 0:
                partner[y], role[y] = x, -1
            break
    return OutwardPairs(partner, role)
