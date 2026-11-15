"""Batch drivers: replay an event list or a note list through a streaming speller.

Not part of the TypeScript package's public API; these mirror prod's evaluation harness so the Python
port can be checked note for note:

- :func:`drive` mirrors ``test/eval/fixtures.ts`` ``drive()`` (event stream; read-back at note-off; in
  look-ahead, every context prod's harness feeds: resolution directions from :func:`enharmonic.resolution_dirs`
  (the next ``horizon`` notes, then the note's own voice), aug6 pairs, local / degree / side keys, dominants).
- :func:`on_notes` mirrors ``onNotes()`` (event stream -> two-pass notes).
- :func:`drive_notes` mirrors ``test/eval/meredith.ts`` ``predict()`` for the streaming modes (a note
  list in bass-first order, the same look-ahead context; releases before strikes at equal t).
- :func:`spell_events` mirrors ``predict()`` in fixtures.ts: one call for any mode.

An event is ``(t, type, midi)`` with ``type`` ``'on'`` or ``'off'``, or a dict with ``t`` (or ``t_ms``),
``type`` and ``midi``; other event types (e.g. ``'respell'``) are ignored, as ``loadEvents`` drops them.
"""

from __future__ import annotations

from typing import Any, Iterable, Literal, Optional, Protocol, Sequence

from .aug6 import outward_pairs
from .key import LocalKey, degree_keys, local_keys
from .dominant import dominant_chords
from .key_side import side_keys
from .resolution import resolution_dirs
from .core import CoreSpeller
from .lof import spelling_of
from .pitch import Pitch
from .speller import Speller
from .two_pass import TwoPassNote, spell_two_pass_lof

Mode = Literal["core", "rt", "la", "tp"]
Event = tuple[float, str, int]


class StreamingSpeller(Protocol):
    look_ahead: bool

    def note_on(self, midi: int, t: Optional[float] = None, resolve_dir: int = 0, resolve_exact: int = 0,
                outward_partner: Optional[int] = None, outward_role: int = 0,
                local_key: Optional[LocalKey] = None, degree_key: Optional[LocalKey] = None, **ctx: Any) -> None: ...
    def note_off(self, midi: int, t: Optional[float] = None) -> None: ...
    def get_lof(self, midi: int) -> Optional[int]: ...


def normalize_events(events: Iterable[Any]) -> list[Event]:
    """Keep on/off events as ``(t, type, midi)`` tuples, in order."""
    out: list[Event] = []
    for e in events:
        if isinstance(e, dict):
            typ = e["type"]
            if typ != "on" and typ != "off":
                continue
            out.append((e["t"] if "t" in e else e["t_ms"], typ, e["midi"]))
        else:
            t, typ, midi = e
            if typ != "on" and typ != "off":
                continue
            out.append((t, typ, midi))
    return out


def _pitch(n: Optional[int], midi: int) -> Optional[Pitch]:
    if n is None:
        return None
    sp = spelling_of(n)
    return Pitch(sp.step, sp.alter, midi // 12 - 1)


def drive_lof(s: StreamingSpeller, events: Sequence[Event], horizon: int = 16) -> list[Optional[int]]:
    """:func:`drive`, returning line-of-fifths positions (None = never read back)."""
    out: list[Optional[int]] = []
    pending: dict[int, list[int]] = {}
    la = bool(getattr(s, "look_ahead", False))
    # Look-ahead also gets each note's aug6 outward partner, its local key and its key-degree key (fixtures.ts
    # drive).
    notes = on_notes(events) if la else []
    pairs = outward_pairs(notes) if la else None
    keys = local_keys(notes) if la else None
    deg_keys = degree_keys(notes) if la else None
    sides = side_keys(notes) if la else None
    doms = dominant_chords(notes) if la else None
    rd = resolution_dirs(notes, window=horizon) if la else None
    on_idx = 0
    for i in range(len(events)):
        t, typ, midi = events[i]
        if typ == "on":
            d = rd.dir[on_idx] if rd is not None else 0
            exact = rd.exact[on_idx] if rd is not None else 0
            y = pairs.partner[on_idx] if pairs is not None else -1
            k = keys[on_idx] if keys is not None else None
            dk = deg_keys[on_idx] if deg_keys is not None else None
            if not la:
                s.note_on(midi, t, d, exact)
            else:
                s.note_on(midi, t, d, exact, notes[y].midi if y >= 0 else None,
                          pairs.role[on_idx] if y >= 0 else 0, k, dk,  # type: ignore[union-attr]
                          side_key=sides[on_idx], dominant=doms[on_idx])  # type: ignore[index]
            on_idx += 1
            pending.setdefault(midi, []).append(len(out))
            out.append(None)
        else:
            q = pending.get(midi)
            if q:
                out[q.pop(0)] = s.get_lof(midi)
            s.note_off(midi, t)
    return out


def drive(s: StreamingSpeller, events: Iterable[Any], horizon: int = 16) -> list[Optional[Pitch]]:
    """Drive a streaming speller over events; one spelling per on-event, read back at its note-off."""
    evs = normalize_events(events)
    lofs = drive_lof(s, evs, horizon)
    midis = [m for _, typ, m in evs if typ == "on"]
    return [_pitch(n, m) for n, m in zip(lofs, midis)]


def on_notes(events: Iterable[Any]) -> list[TwoPassNote]:
    """Collapse an event list to two-pass notes in on-event order (unmatched notes get t_off = t_on)."""
    notes: list[list[Any]] = []
    open_: dict[int, list[int]] = {}
    for t, typ, midi in normalize_events(events):
        if typ == "on":
            open_.setdefault(midi, []).append(len(notes))
            notes.append([midi, t, t])
        else:
            q = open_.get(midi)
            if q:
                notes[q.pop(0)][2] = t
    return [TwoPassNote(m, a, b) for m, a, b in notes]


def make_speller(mode: Mode) -> StreamingSpeller:
    if mode == "core":
        return CoreSpeller()
    if mode == "rt":
        return Speller()
    if mode == "la":
        return Speller(look_ahead=True)
    raise ValueError(f"no streaming speller for mode {mode!r}")


def spell_events_lof(events: Iterable[Any], mode: Mode) -> list[Optional[int]]:
    """Line-of-fifths spelling per on-event for ``mode`` (mirrors fixtures.ts ``predict``)."""
    evs = normalize_events(events)
    if mode == "tp":
        return spell_two_pass_lof(on_notes(evs))
    return drive_lof(make_speller(mode), evs)


def spell_events(events: Iterable[Any], mode: Mode = "la") -> list[Optional[Pitch]]:
    """One spelling per on-event, for ``mode`` in ``core`` / ``rt`` / ``la`` / ``tp``."""
    evs = normalize_events(events)
    midis = [m for _, typ, m in evs if typ == "on"]
    return [_pitch(n, m) for n, m in zip(spell_events_lof(evs, mode), midis)]


# ── note-list driver (meredith.ts) ─────────────────────────────────────────────


def drive_notes_lof(s: StreamingSpeller, notes: Sequence[TwoPassNote], look_ahead: bool) -> list[Optional[int]]:
    """meredith.ts ``predict`` for a streaming speller. ``notes`` must be sorted bass-first."""
    rd = resolution_dirs(notes) if look_ahead else None
    dirs = rd.dir if rd is not None else None
    exact = rd.exact if rd is not None else None
    pairs = outward_pairs(notes) if look_ahead else None
    keys = local_keys(notes) if look_ahead else None
    deg_keys = degree_keys(notes) if look_ahead else None
    sides = side_keys(notes) if look_ahead else None
    doms = dominant_chords(notes) if look_ahead else None
    evs: list[tuple[float, bool, int, int]] = []
    for i, x in enumerate(notes):
        evs.append((x.t_on, True, x.midi, i))
        evs.append((x.t_off, False, x.midi, i))
    evs.sort(key=lambda e: (e[0], e[1], e[2]))
    pred: list[Optional[int]] = [None] * len(notes)
    pend: dict[int, list[int]] = {}
    for t, on, midi, i in evs:
        if on:
            d = dirs[i] if dirs is not None else 0
            x = exact[i] if exact is not None else 0
            y = pairs.partner[i] if pairs is not None else -1
            k = keys[i] if keys is not None else None
            dk = deg_keys[i] if deg_keys is not None else None
            if not look_ahead:
                s.note_on(midi, t, d, x)
            else:
                s.note_on(midi, t, d, x, notes[y].midi if y >= 0 else None,
                          pairs.role[i] if y >= 0 else 0, k, dk,  # type: ignore[union-attr]
                          side_key=sides[i], dominant=doms[i])  # type: ignore[index]
            pend.setdefault(midi, []).append(i)
        else:
            q = pend.get(midi)
            if q:
                pred[q.pop(0)] = s.get_lof(midi)
            s.note_off(midi, t)
    return pred


def spell_notes_lof(notes: Sequence[TwoPassNote], mode: Mode) -> list[Optional[int]]:
    """meredith.ts ``predict``: a bass-first note list, any mode."""
    if mode == "tp":
        return spell_two_pass_lof(notes)
    return drive_notes_lof(make_speller(mode), notes, mode == "la")
