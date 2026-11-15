"""A DOMINANT NAMES ITS TENDENCY TONES (look-ahead context; port of ``src/dominant.ts``).

When a major triad or dominant seventh sounds and the next chord with a different root has its root a fifth below,
the chord is a dominant of what follows, and its two tendency tones take the spelling that makes them its tones: the
third (the leading tone of the target) and the seventh (the tone that falls to the target's third). The root and
fifth are not renamed; they are the reference the third and seventh are named from. Example: Chopin Op. 28 No. 4 m4,
E7 -> Am7: the G# is E7's third, not Ab (the falling line G#->G would otherwise vote Ab).

:func:`dominant_chords` reads it from the notes alone (the pitch classes that SOUND at each note's onset, and the
chords that follow), like ``degree_keys``; the engine applies it (``dominant`` of :meth:`enharmonic.Speller.note_on`).

REACH is a theory parameter, not a latency budget: how far ahead the chord a fifth below may lie and still make the
sounding chord its dominant. It is counted in ONSETS, so a dense texture does not shorten it, and it may therefore
look past the look-ahead's 16-note buffer.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional, Sequence


class DominantChord(NamedTuple):
    """A dominant of what follows: its root pitch class, and each chord tone's pitch class -> its line-of-fifths
    offset from the root (root 0, fifth +1, third +4, seventh -2)."""

    root: int
    offs: dict[int, int]


#: The default reach: 16 onsets after the dominant's own.
DOMINANT_REACH = 16

#: (semitones, fifths) above the root, per chord; dict order is the TS object's.
_TEMPLATES: dict[str, tuple[tuple[int, int], ...]] = {
    "maj": ((0, 0), (4, 4), (7, 1)),
    "min": ((0, 0), (3, -3), (7, 1)),
    "dim": ((0, 0), (3, -3), (6, -6)),
    "aug": ((0, 0), (4, 4), (8, 8)),
    "dom7": ((0, 0), (4, 4), (7, 1), (10, -2)),
    "maj7": ((0, 0), (4, 4), (7, 1), (11, 5)),
    "min7": ((0, 0), (3, -3), (7, 1), (10, -2)),
    "hdim7": ((0, 0), (3, -3), (6, -6), (10, -2)),
    "dim7": ((0, 0), (3, -3), (6, -6), (9, -9)),
}


def chord_of(pcs: set[int]) -> Optional[tuple[str, int]]:
    """The chord a pitch-class set best matches, as (name, root): the root must sound and at least 2 template tones;
    ranked by (template complete, matched - extras, matched, -extras, template size), the first best kept."""
    best: Optional[tuple[tuple[int, ...], str, int]] = None
    size = len(pcs)
    for name, tones in _TEMPLATES.items():
        for r in range(12):
            if r not in pcs:
                continue
            tp = {(r + s) % 12 for s, _ in tones}
            m = sum(1 for p in tp if p in pcs)
            if m < 2:
                continue
            ex = size - m
            key = (1 if m == len(tones) else 0, m - ex, m, -ex, len(tones))
            if best is None or key > best[0]:
                best = (key, name, r)
    return None if best is None else (best[1], best[2])


def dominant_chords(notes: Sequence[Any], reach: int = DOMINANT_REACH) -> list[Optional[DominantChord]]:
    """Per note (input order): the dominant sounding at its onset when the note's pitch class is one of its tones,
    else None. The chord at an onset is every pitch class ringing there (at least 3 for the dominant, 2 for the chord
    after it); dim7 and augmented chords name no root and are skipped while scanning ahead. ``notes`` have ``midi``,
    ``t_on`` and ``t_off``."""
    n = len(notes)
    idx = sorted(range(n), key=lambda i: (notes[i].t_on, notes[i].midi))
    # Distinct onset times, and for each the position (in idx) of its first note.
    times: list[float] = []
    first_at: list[int] = []
    for k, i in enumerate(idx):
        t = notes[i].t_on
        if not times or times[-1] != t:
            times.append(t)
            first_at.append(k)
    # The pitch classes sounding at each onset: notes with t_on <= t < t_off, plus the onset's own notes.
    sound: list[set[int]] = []
    live: list[int] = []
    for j, t in enumerate(times):
        end = first_at[j + 1] if j + 1 < len(times) else n
        for k in range(first_at[j], end):
            live.append(idx[k])
        live = [i for i in live if not (notes[i].t_off <= t and notes[i].t_on < t)]
        sound.append({notes[i].midi % 12 for i in live})
    cache: dict[int, Optional[tuple[str, int]]] = {}

    def chord_at(j: int, lo: int) -> Optional[tuple[str, int]]:
        s = sound[j]
        if len(s) < lo:
            return None
        if j not in cache:
            cache[j] = chord_of(s)
        return cache[j]

    at_onset: list[Optional[DominantChord]] = []
    for j in range(len(times)):
        c = chord_at(j, 3)
        v: Optional[DominantChord] = None
        if c is not None and c[0] in ("maj", "dom7"):
            # The next chord with a different root within `reach` onsets decides: a fifth below makes c its dominant.
            j2 = j + 1
            while j2 < len(times) and j2 - j <= reach:
                c2 = chord_at(j2, 2)
                j2 += 1
                if c2 is None or c2[0] == "aug" or c2[0] == "dim7" or c2[1] == c[1]:
                    continue
                if (c2[1] - c[1]) % 12 == 5:
                    v = DominantChord(c[1], {(c[1] + s) % 12: f for s, f in _TEMPLATES[c[0]]})
                break
        at_onset.append(v)
    out: list[Optional[DominantChord]] = [None] * n
    j = -1
    for k, i in enumerate(idx):
        if j + 1 < len(times) and first_at[j + 1] == k:
            j += 1
        v = at_onset[j]
        if v is not None and notes[i].midi % 12 in v.offs:
            out[i] = v
    return out
