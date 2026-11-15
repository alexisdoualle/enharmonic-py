"""Key -> side: the enharmonic side the look-ahead tier holds, read from the local key's conventional signature (port
of ``src/key-side.ts``).

Proposition: a passage is notated on the side of the conventional key signature of its local key. Db major sounds ->
the side is Db's (five flats): Db-F-Ab, not C#-E#-G#. The key is read from what sounds, mapped to its relative
major's fewest-accidental tonic on the line of fifths (C = 0, G = +1, F = -1, Db = -5), and the speller re-centres
its fold on it (``Speller.set_key``) whenever it changes. The engine never reads a key signature; the side is still
decided by the notes.

:func:`side_keys` reads it from the notes alone, LOOK-AHEAD LEGAL: each note sees the last 96 onsets plus the next
16 notes, no more. Three parts:

- KEY: correlation of that window's pitch-class counts with a primary-triads table (tonic and fifth 4, third 3, the
  collection's other tones 2, the rest 0; major on the diatonic collection, minor on the harmonic minor), best of
  the 24 keys. A table from theory, but a table: provisional.
- CONFIRMATION: a side change is taken only when the next 16 notes sound the new key's own notes (outside the old
  key's collection) more often than the old key's own notes. A passing tonicization does not move the side; a
  modulation does.
- DEEPENING: a modulation goes along the spiral from the current side, up to 7 accidentals: from B major (+5) the
  key a fifth up stays sharp (C# major, +7) instead of folding to Db (-5).

The F#/Gb tie (six accidentals either way) is returned as NaN; the speller resolves it to the side nearer its
current surface.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from .key import KeyNote, LocalKey, _MARGIN, _centre, best_key_centred, score_rows

_DIATONIC, _HARMONIC_MINOR = (0, 2, 4, 5, 7, 9, 11), (0, 2, 3, 5, 7, 8, 11)


def _primary_triads(coll: tuple[int, ...], triad: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(4 if i in (0, 7) else 3 if i in triad else 2 if i in coll else 0 for i in range(12))


#: The primary-triads table, centred and with its squared norm, for major and minor.
_TABLE = (_centre(_primary_triads(_DIATONIC, (0, 4, 7))), _centre(_primary_triads(_HARMONIC_MINOR, (0, 3, 7))))
_ROWS = score_rows(_TABLE)

#: Relative-major tonic pitch class -> its fewest-accidental line-of-fifths x (NaN: F#/Gb, six either way).
_PC_TO_X = (0, -5, 2, -3, 4, -1, math.nan, 1, -4, 3, -2, 5)
#: Look-ahead window: onsets behind, notes ahead; confirmation span; deepening limit.
_BEHIND_ONSETS, _AHEAD_NOTES, _CONFIRM_NOTES, _DEEPEN = 96, 16, 16, 7


def _x_of(k: LocalKey) -> float:
    return _PC_TO_X[(k.tonic + (3 if k.minor else 0)) % 12]


def _in_collection(pc: int, k: LocalKey) -> bool:
    """A key's collection: major diatonic; minor natural plus the raised 6th and 7th."""
    d = (pc - k.tonic) % 12
    return (d != 1 and d != 4 and d != 6) if k.minor else d in _DIATONIC


class _Scan:
    """Pitch-class prefix counts over the notes, each note's onset (first note, exclusive end), and the prefix sums
    of the fast path's key scores."""

    def __init__(self, notes: Sequence[KeyNote]) -> None:
        n = self.n = len(notes)
        pcs = self.pc = [x.midi % 12 for x in notes]
        pre = self.pre = [[0] * 12]
        pre_s = self.pre_s = [[0.0] * 24]
        for q in pcs:
            h = list(pre[-1])
            h[q] += 1
            pre.append(h)
            row = _ROWS[q]
            pre_s.append([a + b for a, b in zip(pre_s[-1], row)])
        self.onset_of: list[int] = []
        self.first: list[int] = []
        self.end: list[int] = []
        for i in range(n):
            if i == 0 or notes[i].t_on != notes[i - 1].t_on:
                self.first.append(i)
                self.end.append(i)
            self.end[-1] = i + 1
            self.onset_of.append(len(self.first) - 1)

    def key(self, a: int, b: int) -> Optional[LocalKey]:
        """The best key of the notes [a, b) (TS ``bestKey(hist(a, b))``): from the prefix-summed scores when the best
        key clears the runner-up by more than the margin, else by the exact computation."""
        a = max(0, a)
        b = min(self.n, b)
        if b <= a:
            return None
        pa, pb = self.pre[a], self.pre[b]
        h = [pb[q] - pa[q] for q in range(12)]
        tot = b - a
        dx = sum(v * v for v in h) - tot * tot / 12
        if dx > 0:
            sc = [y - x for x, y in zip(self.pre_s[a], self.pre_s[b])]
            best = max(sc)
            k = sc.index(best)
            sc[k] = -math.inf
            if (best - max(sc)) / math.sqrt(dx) > _MARGIN:
                return LocalKey(k >> 1, bool(k & 1))
        return best_key_centred(h, _TABLE)


def _deepen(keys: Sequence[Optional[LocalKey]], key_return: bool = False) -> list[Optional[float]]:
    """DEEPENING: each new key goes along the spiral from the current side, within +-7 accidentals.

    ``key_return`` (two-pass only): a key written on the near side only because its far reading is past the +-7
    limit is a readability respell. When the music returns to the key in force before it, that key keeps its prior
    side. Pathetique ii: Ab minor (-7) -> Fb major, forced to E major (+4) -> Ab minor, read -7 again, not G# minor
    (+5). A parallel key (same tonic), or the parallel of the relative, of the forced key or of the key just before
    keeps that key's tonic letter, so it is never a return; such a candidate disarms the pending return."""
    prev: Optional[float] = None
    last_raw: Optional[float] = None
    last_out: Optional[float] = None
    # key_return: the key just before (held), and, once the limit forces a respell, the key in force before it (pre)
    # and the forced key itself, each with its side.
    held: Optional[tuple[LocalKey, float]] = None
    pre: Optional[tuple[LocalKey, float]] = None
    forced: Optional[tuple[LocalKey, float]] = None

    def parallel(a: Optional[tuple[LocalKey, float]], k: LocalKey) -> bool:
        return a is not None and (a[0].tonic == k.tonic or (a[0].tonic + (3 if a[0].minor else 9)) % 12 == k.tonic)

    res: list[Optional[float]] = []
    for k in keys:
        if k is None:
            res.append(None)
            continue
        x = _x_of(k)
        if last_raw is not None and x == last_raw:  # NaN never equals
            res.append(last_out)
            continue
        last_raw = x
        if pre is not None and prev is not None and k.tonic == pre[0].tonic and k.minor == pre[0].minor:
            ret = None if (parallel(forced, k) or parallel(held, k)) else pre[1]
            pre = forced = None
            if ret is not None:
                prev = last_out = ret
                held = (k, ret)
                res.append(ret)
                continue
        if prev is None:
            out = x
        else:
            cands = [6, -6] if math.isnan(x) else [x, x + 12, x - 12]
            ok = [c for c in cands if abs(c) <= _DEEPEN]
            out = _near(ok if ok else cands, prev)
            if key_return and not math.isnan(x) and _near(cands, prev) != out and held is not None:
                pre = held
                forced = (k, out)
        if not math.isnan(out):
            prev = out
        last_out = out
        held = (k, out)
        res.append(out)
    return res


def _near(cs: Sequence[float], p: float) -> float:
    """``cs.reduce((a, c) => |c - p| < |a - p| ? c : a)``: the first candidate nearest ``p``."""
    a = cs[0]
    for c in cs[1:]:
        if abs(c - p) < abs(a - p):
            a = c
    return a


def side_keys(notes: Sequence[KeyNote]) -> list[Optional[float]]:
    """Each note's side key (same order as the input): the relative major's tonic on the line of fifths, NaN for the
    F#/Gb tie, None before any key can be read. Notes must be in onset order (co-struck notes share ``t_on``). Pass
    it as :meth:`enharmonic.Speller.note_on`'s ``side_key``."""
    S = _Scan(notes)
    n = S.n
    # KEY on the last 96 onsets, the note's own onset, and the next 16 notes.
    read: list[Optional[LocalKey]] = []
    for i in range(n):
        o = S.onset_of[i]
        read.append(S.key(S.first[max(0, o - _BEHIND_ONSETS)], max(S.end[o], min(n, i + _AHEAD_NOTES + 1))))
    # CONFIRMATION: move to a new side only when the next 16 notes favour the new key's own notes.
    keys: list[Optional[LocalKey]] = []
    cur: Optional[LocalKey] = None
    pcs = S.pc
    for i in range(n):
        r = read[i]
        if r is None or cur is None:
            if r is not None:
                cur = r
            keys.append(cur)
            continue
        xr, xc = _x_of(r), _x_of(cur)
        if xr == xc or (math.isnan(xr) and math.isnan(xc)):
            cur = r
            keys.append(cur)
            continue
        score = 0
        for j in range(i, min(n, i + _CONFIRM_NOTES + 1)):
            a, b = _in_collection(pcs[j], r), _in_collection(pcs[j], cur)
            if a and not b:
                score += 1
            elif b and not a:
                score -= 1
        if score > 0:
            cur = r
        keys.append(cur)
    return _deepen(keys)


#: :func:`centred_side_keys`: onsets read on each side of the note.
CENTRED_SIDE_RADIUS = 48


def _centred_keys(notes: Sequence[KeyNote], radius: int) -> list[Optional[LocalKey]]:
    """Each note's key read on the onsets centred on it (+-radius), before deepening."""
    S = _Scan(notes)
    last = len(S.first) - 1
    keys: list[Optional[LocalKey]] = []
    for i in range(S.n):
        o = S.onset_of[i]
        keys.append(S.key(S.first[max(0, o - radius)], S.end[min(last, o + radius)]))
    return keys


def centred_side_keys(notes: Sequence[KeyNote], radius: int = CENTRED_SIDE_RADIUS,
                      key_return: bool = True) -> list[Optional[float]]:
    """OFFLINE side keys, for the two-pass tier: each note's key is read on the onsets centred on it (+-48 onsets, the
    note's own included), with the same table and deepening as :func:`side_keys` and no confirmation step (a centred
    window already sees the passage on both sides). Same output convention: the relative major's tonic on the line
    of fifths (deepened up to +-7), NaN for the F#/Gb tie, None for none. Not look-ahead legal. ``key_return``
    (default True): a return to the key in force before a limit-forced respell keeps that key's side (see
    ``_deepen``; ``TwoPassOptions.key_return``)."""
    return _deepen(_centred_keys(notes, radius), key_return)

