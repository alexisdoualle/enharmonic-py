"""The local key around a note: the context the engine's leading-tone and key-degree gates read (port of
``src/key.ts``).

Proposition: a leading tone of the local key (its sharp 4, leading to the dominant; in minor also the raised 6th
and 7th) is named by the key, and a falling chromatic line from it cancels it (C#->C in D minor stays C#, not the
look-ahead's letter step Db->C).

:func:`local_keys` reads the key from the notes alone, on a window of onsets CENTRED on each note (it needs the
next few onsets, so it is look-ahead or offline context, like ``resolve_dir``): the Pearson correlation of the
window's pitch-class counts with a key table rotated to each of the 24 keys, best key wins.

Two tables:

- ``"temperley"``: Temperley's key profiles, pitch-class distributions of the Kostka-Payne textbook corpus
  (D. Temperley, *Music and Probability*, MIT Press 2007, ch. 4; the same numbers as music21's
  ``TemperleyKostkaPayne``). A fitted profile, kept for comparison; no tier reads it by default.
- ``"tonic-triad"``: from theory, no fitted numbers. "A key is its tonic triad inside its seven-note collection":
  tonic-triad tones 3, the collection's other tones 2, the rest 0; major uses the diatonic collection, minor the
  harmonic minor. Both look-ahead and two-pass read it on +-12 onsets (the default). At +-6 it ties Temperley;
  the wider window reads the surrounding key instead of a brief tonicization.

The key-degree gate (:func:`is_key_degree`, engine option ``key_degree_gate``) reads its own key, the tonic-triad
table on +-12 NOTES centred on the note (:func:`degree_keys`), on both look-ahead and two-pass.
"""

from __future__ import annotations

import math
from typing import Literal, NamedTuple, Optional, Protocol, Sequence


class LocalKey(NamedTuple):
    """A local key: its tonic's pitch class (C = 0) and mode."""

    tonic: int
    minor: bool


class KeyNote(Protocol):
    midi: int
    t_on: float


KeyTable = Literal["temperley", "tonic-triad"]
#: What ``radius`` counts: ``"onsets"`` (co-struck notes are one onset) or ``"notes"``, the notes of the input
#: order on each side of the note itself (the input must then be in onset order, co-struck notes in any fixed order).
KeyUnit = Literal["onsets", "notes"]

_TEMPERLEY = (
    (0.748, 0.060, 0.488, 0.082, 0.670, 0.460, 0.096, 0.715, 0.104, 0.366, 0.057, 0.400),
    (0.712, 0.084, 0.474, 0.618, 0.049, 0.460, 0.105, 0.747, 0.404, 0.067, 0.133, 0.330),
)
_DIATONIC, _HARMONIC_MINOR = (0, 2, 4, 5, 7, 9, 11), (0, 2, 3, 5, 7, 8, 11)
_MAJOR_TRIAD, _MINOR_TRIAD = (0, 4, 7), (0, 3, 7)


def _tonic_triad(collection: tuple[int, ...], triad: tuple[int, ...]) -> tuple[int, ...]:
    """Tonic triad 3, other collection tones 2, the rest 0."""
    return tuple(3 if i in triad else 2 if i in collection else 0 for i in range(12))


_TABLES = {
    "temperley": _TEMPERLEY,
    "tonic-triad": (_tonic_triad(_DIATONIC, _MAJOR_TRIAD), _tonic_triad(_HARMONIC_MINOR, _MINOR_TRIAD)),
}


def _centre(y: Sequence[float]) -> tuple[list[float], float]:
    """A template centred on its mean (y - mean) and its sum of squares, summed left to right as in JS."""
    # Array.reduce((a, b) => a + b) starts from y[0] and adds left to right.
    acc = y[0]
    for v in y[1:]:
        acc = acc + v
    my = acc / 12
    yc = [v - my for v in y]
    dy = 0.0
    for i in range(12):
        dy += yc[i] * yc[i]
    return yc, dy


_CENTRED = {k: tuple(_centre(m) for m in modes) for k, modes in _TABLES.items()}


def _best_key(h: Sequence[int], table: str) -> Optional[LocalKey]:
    """The best key of a 12-bin pitch-class COUNT histogram (Pearson correlation with each mode's template, the
    histogram rotated to each tonic; 0 when flat), or None if empty. Ties go to the first key in the order
    C major, C minor, C# major, ..., B minor. Every float sum runs in the TypeScript order (i = 0..11)."""
    return best_key_centred(h, _CENTRED[table])


def best_key_centred(h: Sequence[int], centred: Sequence[tuple[list[float], float]]) -> Optional[LocalKey]:
    """:func:`_best_key` for a centred table (``_centre`` of the major and minor templates)."""
    tot = 0
    for v in h:
        tot += v
    if not tot:
        return None
    mx = tot / 12
    (y0, d0), (y1, d1) = centred
    xs = [v - mx for v in h]
    tonic, minor, br = -1, False, -math.inf
    for t in range(12):
        xc = xs[t:] + xs[:t]
        dx = 0.0
        n0 = 0.0
        n1 = 0.0
        for v, a, b in zip(xc, y0, y1):
            dx += v * v
            n0 += v * a
            n1 += v * b
        r = n0 / math.sqrt(dx * d0) if dx and d0 else 0
        if r > br:
            br, tonic, minor = r, t, False
        r = n1 / math.sqrt(dx * d1) if dx and d1 else 0
        if r > br:
            br, tonic, minor = r, t, True
    return LocalKey(tonic, minor)


#: Per pitch class, its contribution to every key's correlation numerator, scaled by the mode's 1/sqrt(dy): row[k]
#: for key k = 2 * tonic + minor is yc_m[(pc - tonic) % 12] / sqrt(dy_m). A window's sum of rows ranks the 24 keys
#: as its correlations do (up to the positive factor 1/sqrt(dx) and rounding), for the fast path below.
def score_rows(modes: Sequence[tuple[list[float], float]]) -> list[list[float]]:
    """Per pitch class, its contribution to every key's correlation numerator (see ``_ROWS``)."""
    return [[m[0][(pc - t) % 12] / math.sqrt(m[1]) for t in range(12) for m in modes] for pc in range(12)]


_ROWS = {tb: score_rows(modes) for tb, modes in _CENTRED.items()}
#: The fast path decides only when the best key's correlation beats the runner-up by more than this; otherwise
#: (a near or exact tie) the exact computation, in the TypeScript order, decides. Rounding is ~1e-12 at most.
_MARGIN = 1e-7


def _units(notes: Sequence[KeyNote], unit: KeyUnit) -> tuple[list[list[int]], list[int]]:
    """The window units in order (onsets in time order, or the notes in input order), their pitch-class counts,
    and each note's unit."""
    n = len(notes)
    if unit == "notes":
        counts = []
        for x in notes:
            c = [0] * 12
            c[x.midi % 12] = 1
            counts.append(c)
        return counts, list(range(n))
    idx = sorted(range(n), key=lambda i: notes[i].t_on)
    counts = []
    unit_of = [0] * n
    last_t: Optional[float] = None
    for i in idx:
        if last_t is None or notes[i].t_on != last_t:
            counts.append([0] * 12)
            last_t = notes[i].t_on
        counts[-1][notes[i].midi % 12] += 1
        unit_of[i] = len(counts) - 1
    return counts, unit_of


def local_keys(notes: Sequence[KeyNote], table: KeyTable = "tonic-triad", radius: int = 12,
               unit: KeyUnit = "onsets") -> list[Optional[LocalKey]]:
    """Each note's local key (same order as the input): the best key of the pitch classes struck in the onsets from
    ``radius`` before the note's onset to ``radius`` after it, one count per note. Onsets are exact ``t_on``
    values. Notes need not be sorted, unless ``unit`` is ``"notes"`` (then ``radius`` counts notes of the input
    order on each side of the note). Defaults: the look-ahead tier's table and window."""
    if unit == "notes":
        return _note_keys(notes, table, radius)
    counts, unit_of = _units(notes, unit)
    rows = _ROWS[table]
    # Prefix sums over the units: pitch-class counts (exact integers) and key scores (the fast path's).
    pre = [[0] * 12]
    pre_s = [[0.0] * 24]
    for c in counts:
        p, ps = pre[-1], pre_s[-1]
        pre.append([p[q] + c[q] for q in range(12)])
        for q in range(12):
            if c[q]:
                v, row = c[q], rows[q]
                ps = [a + v * b for a, b in zip(ps, row)]
        pre_s.append(ps)
    keys: list[Optional[LocalKey]] = [None] * len(counts)
    last = len(counts) - 1
    for o in range(len(counts)):
        a, b = max(0, o - radius), min(last, o + radius) + 1
        pa, pb = pre[a], pre[b]
        h = [pb[q] - pa[q] for q in range(12)]
        tot = sum(h)
        dx = sum(v * v for v in h) - tot * tot / 12
        key: Optional[LocalKey] = None
        if dx > 0:
            sc = [y - x for x, y in zip(pre_s[a], pre_s[b])]
            best = max(sc)
            k = sc.index(best)
            sc[k] = -math.inf
            if (best - max(sc)) / math.sqrt(dx) > _MARGIN:
                key = LocalKey(k >> 1, bool(k & 1))
        keys[o] = key if key is not None else _best_key(tuple(h), table)
    return [keys[u] for u in unit_of]


def _note_keys(notes: Sequence[KeyNote], table: KeyTable, radius: int) -> list[Optional[LocalKey]]:
    """:func:`local_keys` with ``unit="notes"``: the window slides one note at a time, so its pitch-class counts
    (exact integers) and key scores (the fast path's) are updated by one note in and one out, never rebuilt.
    Same certificate as the onset path: a near tie goes to the exact computation."""
    n = len(notes)
    pcs = [x.midi % 12 for x in notes]
    rows = _ROWS[table]
    h = [0] * 12
    sc = [0.0] * 24
    tot = sq = 0
    hi = 0  # notes [lo, hi) are in the window
    lo = 0
    out: list[Optional[LocalKey]] = [None] * n
    for i in range(n):
        a, b = max(0, i - radius), min(n, i + radius + 1)
        while hi < b:
            q = pcs[hi]
            sq += 2 * h[q] + 1
            h[q] += 1
            tot += 1
            sc = [x + y for x, y in zip(sc, rows[q])]
            hi += 1
        while lo < a:
            q = pcs[lo]
            h[q] -= 1
            sq -= 2 * h[q] + 1
            tot -= 1
            sc = [x - y for x, y in zip(sc, rows[q])]
            lo += 1
        dx = sq - tot * tot / 12
        key: Optional[LocalKey] = None
        if dx > 0:
            best = max(sc)
            k = sc.index(best)
            second = max(sc[:k] + sc[k + 1:])
            if (best - second) / math.sqrt(dx) > _MARGIN:
                key = LocalKey(k >> 1, bool(k & 1))
        out[i] = key if key is not None else _best_key(tuple(h), table)
    return out


def local_keys_exact(notes: Sequence[KeyNote], table: KeyTable = "tonic-triad", radius: int = 12,
                     unit: KeyUnit = "onsets") -> list[Optional[LocalKey]]:
    """:func:`local_keys` with every window decided by the exact computation (no fast path); the reference the
    tests hold the fast path to."""
    counts, unit_of = _units(notes, unit)
    pre = [[0] * 12]
    for c in counts:
        p = pre[-1]
        pre.append([p[q] + c[q] for q in range(12)])
    last = len(counts) - 1
    keys = []
    for o in range(len(counts)):
        pa, pb = pre[max(0, o - radius)], pre[min(last, o + radius) + 1]
        keys.append(_best_key(tuple(pb[q] - pa[q] for q in range(12)), table))
    return [keys[u] for u in unit_of]


def degree_keys(notes: Sequence[KeyNote]) -> list[Optional[LocalKey]]:
    """Each note's key for the key-degree gate (same order as the input, which must be in onset order): the
    tonic-triad table (from theory, no fitted numbers) on the 12 notes on each side of the note. Both the
    look-ahead and the two-pass tier read it."""
    return local_keys(notes, "tonic-triad", 12, "notes")


def is_leading_tone(pc: int, key: LocalKey) -> bool:
    """Whether pitch class ``pc`` is a leading tone of ``key``: in major the sharp 4 (leading to the dominant); in
    minor the sharp 4, the raised 6th and the raised 7th."""
    d = (pc - key.tonic) % 12
    return d in (6, 9, 11) if key.minor else d == 6


def is_key_degree(pc: int, key: LocalKey) -> bool:
    """Whether pitch class ``pc`` is a degree of ``key``: in major its seven diatonic degrees; in minor the natural
    minor plus the raised 6th and 7th."""
    d = (pc - key.tonic) % 12
    return d not in (1, 4, 6) if key.minor else d in (0, 2, 4, 5, 7, 9, 11)


def is_wide_leading_tone(pc: int, key: LocalKey) -> bool:
    """Whether the local key names pitch class ``pc`` a leading tone in the wide sense, the reading the engine's
    held-letter target uses. By semitones above the tonic: major 11 (natural 7), 6 (#4), 1 (#1), 8 (#5) and 2 (the
    2nd, a semitone under b3); minor 11 (#7), 6 (#4), 9 (#6), 1 (#1) and 2 (the 2nd, leading to b3)."""
    d = (pc - key.tonic) % 12
    return d in (11, 6, 9, 1, 2) if key.minor else d in (11, 6, 1, 2, 8)
