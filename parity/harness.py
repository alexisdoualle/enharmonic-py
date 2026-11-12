"""Python side of the parity check: load goldens, replay every input through the port, compare.

Used by ``tests/test_parity.py`` and by ``parity/check.py`` (the report / sync CLI).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from enharmonic.core import CoreSpeller
from enharmonic.drive import drive_lof, normalize_events, on_notes, spell_events_lof, spell_notes_lof
from enharmonic.speller import Speller
from enharmonic.two_pass import TwoPassNote, TwoPassOptions, spell_two_pass_lof

HERE = Path(__file__).resolve().parent
GOLDENS = HERE / "goldens"
INPUTS = HERE / "inputs"

LAB_FIXTURES = Path(os.environ.get("ENHARMONIC_LAB_FIXTURES", Path.home() / "JavaScript/enharmonic-lab/fixtures"))
MEREDITH = Path(os.environ.get("MEREDITH_CORPUS", Path(os.environ.get("ENHARMONIC_PROD", Path.home() / "JavaScript/enharmonic")) / "corpora/meredith"))

Lof = Optional[int]
SETS = ("random", "fixtures", "meredith-clean", "meredith-noisy")


def load_golden(name: str, root: Path = GOLDENS) -> dict[str, Any]:
    with gzip.open(root / f"{name}.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ── modes (mirror parity/export.mts MODES + EXTRA) ─────────────────────────────

def _streaming(factory: Callable[[], Any]) -> Callable[[list], list[Lof]]:
    return lambda ev: drive_lof(factory(), ev)


def _tp(opts: TwoPassOptions) -> Callable[[list], list[Lof]]:
    return lambda ev: spell_two_pass_lof(on_notes(ev), opts)


EVENT_MODES: dict[str, Callable[[list], list[Lof]]] = {
    "core": lambda ev: spell_events_lof(ev, "core"),
    "rt": lambda ev: spell_events_lof(ev, "rt"),
    "la": lambda ev: spell_events_lof(ev, "la"),
    "tp": lambda ev: spell_events_lof(ev, "tp"),
    "rt_tol30": _streaming(lambda: Speller(onset_tolerance=30)),
    "la_tol30": _streaming(lambda: Speller(look_ahead=True, onset_tolerance=30)),
    "rt_key3": _streaming(lambda: Speller(key_tonic=3)),
    "la_keym5": _streaming(lambda: Speller(look_ahead=True, key_tonic=-5)),
    "core_g0_r0": _streaming(lambda: CoreSpeller(0, 3, 0, 0)),
    "core_dap2": _streaming(lambda: CoreSpeller(2, 3, 2, 8)),
    "tp_honor": _tp(TwoPassOptions(honor_resolution=True)),
    "tp_bwoff_m2": _tp(TwoPassOptions(backward_look_ahead="off", centre_margin=2)),
    "tp_nocentre": _tp(TwoPassOptions(centre_tiebreak=False)),
    "tp_tol30": _tp(TwoPassOptions(onset_tolerance=30)),
    "tp_aug6off": _tp(TwoPassOptions(aug6_outward=False, aug6_wedge=False)),
    "tp_ltoff": _tp(TwoPassOptions(leading_tone_gate=False)),
}


# ── inputs ─────────────────────────────────────────────────────────────────────

def random_inputs() -> dict[str, list]:
    with (INPUTS / "random.json").open() as f:
        streams = json.load(f)
    return {k: normalize_events(v) for k, v in streams.items()}


def fixture_events(path: Path) -> list:
    with path.open() as f:
        return normalize_events(json.load(f))


_STEP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_OPNDV = re.compile(r'\(\s*(\d+)\s+"([A-G])([nsf]+)(\d+)"\s+(\d+)\s+(\d+)\s*\)')
TATUM_MS = 125


def parse_opndv(text: str) -> list[tuple[int, int, int]]:
    """meredith.ts ``parseOpndv``: (onset, dur, midi) per note, sorted bass-first (stable)."""
    notes = []
    for m in _OPNDV.finditer(text):
        onset, letter, acc, octave, dur = int(m[1]), m[2], m[3], int(m[4]), int(m[5])
        alter = 0 if acc == "n" else sum(1 if c == "s" else -1 if c == "f" else 0 for c in acc)
        notes.append((onset, dur, (octave + 1) * 12 + _STEP[letter] + alter))
    notes.sort(key=lambda x: (x[0], x[2]))
    return notes


def meredith_notes(text: str) -> list[TwoPassNote]:
    return [TwoPassNote(midi, onset * TATUM_MS, (onset + dur) * TATUM_MS) for onset, dur, midi in parse_opndv(text)]


# ── comparison ─────────────────────────────────────────────────────────────────

@dataclass
class ModeResult:
    set: str
    mode: str
    inputs: int = 0
    notes: int = 0
    mismatches: int = 0
    seconds: float = 0.0
    first: list[str] = field(default_factory=list)


def compare(got: list[Lof], want: list[Lof]) -> list[int]:
    if len(got) != len(want):
        return list(range(max(len(got), len(want))))
    return [i for i, (a, b) in enumerate(zip(got, want)) if a != b]


def iter_set(name: str, golden: dict[str, Any]) -> Iterator[tuple[str, str, Callable[[], list[Lof]], list[Lof]]]:
    """Yield (mode, input id, thunk producing the Python spellings, golden spellings)."""
    modes: dict[str, dict[str, list[Lof]]] = golden["modes"]
    if name == "random":
        inputs = random_inputs()
        for mode, per in modes.items():
            for iid, want in per.items():
                yield mode, iid, (lambda ev=inputs[iid], f=EVENT_MODES[mode]: f(ev)), want
    elif name == "fixtures":
        cache: dict[str, list] = {}

        def ev(iid: str) -> list:
            if iid not in cache:
                cache[iid] = fixture_events(LAB_FIXTURES / iid / "events.json")
            return cache[iid]

        for mode, per in modes.items():
            for iid, want in per.items():
                yield mode, iid, (lambda iid=iid, f=EVENT_MODES[mode]: f(ev(iid))), want
    elif name.startswith("meredith-"):
        variant = name.split("-", 1)[1]
        stem = "opnd-m-noisy" if variant == "noisy" else "opnd-m"
        root = MEREDITH / stem / stem
        ncache: dict[str, list[TwoPassNote]] = {}

        def notes(iid: str) -> list[TwoPassNote]:
            if iid not in ncache:
                ncache[iid] = meredith_notes((root / iid).read_text(encoding="utf-8"))
            return ncache[iid]

        for mode, per in modes.items():
            for iid, want in per.items():
                yield mode, iid, (lambda iid=iid, mode=mode: spell_notes_lof(notes(iid), mode)), want
    else:
        raise ValueError(name)


def input_problems(name: str, golden: dict[str, Any]) -> list[str]:
    """Inputs that are missing or whose content hash differs from the one the goldens were made from."""
    probs: list[str] = []
    if name == "random":
        if sha256_file(INPUTS / "random.json") != golden["inputSha256"]:
            probs.append("parity/inputs/random.json changed since export")
        return probs
    if name == "fixtures":
        root = LAB_FIXTURES
    else:
        stem = "opnd-m-noisy" if name.endswith("noisy") else "opnd-m"
        root = MEREDITH / stem / stem
    for iid, meta in golden["inputs"].items():
        p = root / iid / "events.json" if name == "fixtures" else root / iid
        if not p.exists():
            probs.append(f"missing input {p}")
        elif sha256_file(p) != meta["sha256"]:
            probs.append(f"input changed since export: {p}")
    return probs
