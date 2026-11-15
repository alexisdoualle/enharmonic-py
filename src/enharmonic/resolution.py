"""LOOK UNTIL THE NOTE RESOLVES (look-ahead context; port of ``src/resolution.ts``): where each note resolves, read
from the notes that follow.

The look-ahead's letter-aware vote needs a note's resolution direction (``resolve_dir`` / ``resolve_exact``): +1 when
it moves up a semitone, -1 down, 0 unknown. The first reader is the forward buffer: the first note a semitone away
(any octave for ``resolve_dir``, the exact pitch for ``resolve_exact``) among the next ``window`` notes. When that
finds nothing, a note whose own voice repeats, holds or rests is still waiting to resolve, so the reader follows the
note's OWN voice (its register: the same octave) until it moves by a semitone: notes struck with it are not its
resolution, a re-strike of the note extends the scan, a step of a tone does not end it (the voice may pass on to its
semitone), and the scan stops at ``max`` notes. Mozart Requiem Introitus m45: G# hammered eight times across doubled
parts rises to A 80 notes later; the 16-note buffer sees no semitone and the speller wrote Ab, the own-voice scan
finds the rise and it writes G#.

REACH is a theory parameter, not a latency budget: about one chromatic note in six resolves beyond 16 notes, and
following them pays up to about 128 notes. A live caller who wants the rule exactly must buffer that far, or pass
``mode="window"``.
"""

from __future__ import annotations

from typing import Any, Literal, NamedTuple, Sequence


class ResolutionDirs(NamedTuple):
    """Per note: ``dir`` for ``resolve_dir`` (any octave) and ``exact`` for ``resolve_exact`` (exact pitch)."""

    dir: list[int]
    exact: list[int]


#: The default reach: the 16-note buffer, then the note's own voice up to 128 notes.
RESOLUTION_WINDOW = 16
RESOLUTION_MAX = 128


def _next_step(keys: Sequence[int], up: Sequence[int], down: Sequence[int], horizon: int) -> list[int]:
    """For each a, +1 if the first b in (a, a + horizon] with keys[b] == up[a] or down[a] matches ``up``, -1 if it
    matches ``down``, 0 if there is none (one backward sweep with the next index of every key)."""
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


def resolution_dirs(notes: Sequence[Any], mode: Literal["window", "own-voice"] = "own-voice",
                    window: int = RESOLUTION_WINDOW, max: int = RESOLUTION_MAX) -> ResolutionDirs:
    """Per note (input order, which must be onset order, bass first within a chord): its resolution direction for
    ``resolve_dir`` (``dir``, any octave) and ``resolve_exact`` (``exact``, exact pitch). ``"window"``: only the
    forward buffer of ``window`` notes; ``"own-voice"`` (default): when the buffer finds no semitone, follow the
    note's own voice until it moves by a semitone, up to ``max`` notes; that fills ``dir``, and ``exact`` when the
    buffer left it at 0. ``notes`` have ``midi`` and ``t_on``."""
    n = len(notes)
    midis = [x.midi for x in notes]
    pcs = [m % 12 for m in midis]
    # The buffer: the first note a semitone away among the next `window` (resolve_step / resolve_exact_step, each
    # read independently: the first non-zero of each).
    d = _next_step(pcs, [(p + 1) % 12 for p in pcs], [(p + 11) % 12 for p in pcs], window)
    exact = _next_step(midis, [m + 1 for m in midis], [m - 1 for m in midis], window)
    if mode == "own-voice":
        ton = [x.t_on for x in notes]
        for a in range(n):
            if d[a] == 0:
                d[a] = _own_voice_step(midis, ton, a, max)
                if exact[a] == 0:
                    exact[a] = d[a]
    return ResolutionDirs(d, exact)


def _own_voice_step(midis: Sequence[int], ton: Sequence[Any], a: int, max_: int) -> int:
    """The note's own voice, onset by onset after its own: the first onset that does not re-strike the note and
    strikes a semitone above or below it (same octave) gives the direction (both at once: 0); 0 if none within
    ``max_`` notes."""
    n = len(midis)
    m = midis[a]
    t_a = ton[a]
    b = a + 1
    while b < n and ton[b] == t_a:  # struck with it: not its resolution
        b += 1
    while b < n and b - a <= max_:
        t0 = ton[b]
        e = b
        re = up = down = False
        while e < n and ton[e] == t0:
            dd = midis[e] - m
            if dd == 0:
                re = True
            elif dd == 1:
                up = True
            elif dd == -1:
                down = True
            e += 1
        if not re and (up or down):
            return 0 if (up and down) else 1 if up else -1
        b = e
    return 0
