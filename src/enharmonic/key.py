"""The local key around a note: the context the engine's leading-tone gate reads (port of ``src/key.ts``).

Proposition: a leading tone of the local key (its sharp 4, leading to the dominant; in minor also the raised 6th
and 7th) is named by the key, and a falling chromatic line from it cancels it (C#->C in D minor stays C#, not the
look-ahead's letter step Db->C).

:func:`local_keys` reads the key from the notes alone, on a window of onsets CENTRED on each note (it needs the
next few onsets, so it is look-ahead or offline context, like ``resolve_dir``): the Pearson correlation of the
window's pitch-class counts with a key table rotated to each of the 24 keys, best key wins.

Two tables:

- ``"temperley"``: Temperley's key profiles, pitch-class distributions of the Kostka-Payne textbook corpus
  (D. Temperley, *Music and Probability*, MIT Press 2007, ch. 4; the same numbers as music21's
  ``TemperleyKostkaPayne``). A fitted profile; the look-ahead tier reads it on +-6 onsets.
- ``"tonic-triad"``: from theory, no fitted numbers. "A key is its tonic triad inside its seven-note collection":
  tonic-triad tones 3, the collection's other tones 2, the rest 0; major uses the diatonic collection, minor the
  harmonic minor. The two-pass tier reads it on +-12 onsets.
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


def _best_key(h: tuple[int, ...], table: str) -> Optional[LocalKey]:
    """The best key of a 12-bin pitch-class COUNT histogram (Pearson correlation with each mode's template, the
    histogram rotated to each tonic; 0 when flat), or None if empty. Ties go to the first key in the order
    C major, C minor, C# major, ..., B minor. Every float sum runs in the TypeScript order (i = 0..11)."""
    tot = 0
    for v in h:
        tot += v
    if not tot:
        return None
    mx = tot / 12
    (y0, d0), (y1, d1) = _CENTRED[table]
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
_ROWS = {
    tb: [[m[0][(pc - t) % 12] / math.sqrt(m[1]) for t in range(12) for m in modes] for pc in range(12)]
    for tb, modes in _CENTRED.items()
}
#: The fast path decides only when the best key's correlation beats the runner-up by more than this; otherwise
#: (a near or exact tie) the exact computation, in the TypeScript order, decides. Rounding is ~1e-12 at most.
_MARGIN = 1e-7


def local_keys(notes: Sequence[KeyNote], table: KeyTable = "temperley", radius: int = 6) -> list[Optional[LocalKey]]:
    """Each note's local key (same order as the input): the best key of the pitch classes struck in the onsets from
    ``radius`` before the note's onset to ``radius`` after it, one count per note. Onsets are exact ``t_on``
    values. Notes need not be sorted. Defaults: the look-ahead tier's table and window."""
    n = len(notes)
    idx = sorted(range(n), key=lambda i: notes[i].t_on)
    counts: list[list[int]] = []
    onset_of = [0] * n
    last_t: Optional[float] = None
    for i in idx:
        if last_t is None or notes[i].t_on != last_t:
            counts.append([0] * 12)
            last_t = notes[i].t_on
        counts[-1][notes[i].midi % 12] += 1
        onset_of[i] = len(counts) - 1
    rows = _ROWS[table]
    # Prefix sums over onsets: pitch-class counts (exact integers) and key scores (the fast path's).
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
    return [keys[onset_of[i]] for i in range(n)]


def local_keys_exact(notes: Sequence[KeyNote], table: KeyTable = "temperley", radius: int = 6) -> list[Optional[LocalKey]]:
    """:func:`local_keys` with every window decided by the exact computation (no fast path); the reference the
    tests hold the fast path to."""
    n = len(notes)
    idx = sorted(range(n), key=lambda i: notes[i].t_on)
    counts: list[list[int]] = []
    onset_of = [0] * n
    last_t: Optional[float] = None
    for i in idx:
        if last_t is None or notes[i].t_on != last_t:
            counts.append([0] * 12)
            last_t = notes[i].t_on
        counts[-1][notes[i].midi % 12] += 1
        onset_of[i] = len(counts) - 1
    pre = [[0] * 12]
    for c in counts:
        p = pre[-1]
        pre.append([p[q] + c[q] for q in range(12)])
    last = len(counts) - 1
    keys = []
    for o in range(len(counts)):
        pa, pb = pre[max(0, o - radius)], pre[min(last, o + radius) + 1]
        keys.append(_best_key(tuple(pb[q] - pa[q] for q in range(12)), table))
    return [keys[onset_of[i]] for i in range(n)]


def is_leading_tone(pc: int, key: LocalKey) -> bool:
    """Whether pitch class ``pc`` is a leading tone of ``key``: in major the sharp 4 (leading to the dominant); in
    minor the sharp 4, the raised 6th and the raised 7th."""
    d = (pc - key.tonic) % 12
    return d in (6, 9, 11) if key.minor else d == 6
