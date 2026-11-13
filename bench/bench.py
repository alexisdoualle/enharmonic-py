"""Benchmark the speller on the Meredith corpus (216 movements, 195,972 notes per variant).

    python bench/bench.py                         # clean + noisy, all modes, 3 repeats
    python bench/bench.py --sets clean --modes rt,la --repeats 5
    python bench/bench.py --json out.json         # also write the numbers as JSON

Each mode runs over the whole corpus ``--repeats`` times; the median is reported. Timings are split:

- ``parse``: reading and parsing the .opnd files into notes (done once, outside every timed run);
- ``driver``: the batch driver alone (event sort, look-ahead directions, read-back), measured by running
  it with a no-op speller; streaming modes only;
- ``speller``: ``total - driver`` for streaming modes; for two-pass, the two engine passes (``passes``)
  and everything else (``merge``: ordering, directions, reversal, reconciliation).

Notes/second is computed on ``total``. Speed depends on the CPU, the Python build and machine load; compare
runs only on the same machine.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "parity"))

from enharmonic import two_pass  # noqa: E402
from enharmonic.drive import drive_notes_lof, spell_notes_lof  # noqa: E402
from harness import MEREDITH, meredith_notes  # noqa: E402

MODES = ("core", "rt", "la", "tp")


def machine() -> dict[str, Any]:
    def sysctl(k: str) -> Optional[str]:
        try:
            return subprocess.run(["sysctl", "-n", k], capture_output=True, text=True, check=True).stdout.strip()
        except Exception:
            return None

    return {
        "cpu": sysctl("machdep.cpu.brand_string") or platform.processor() or platform.machine(),
        "cores": os.cpu_count(),
        "python": f"{platform.python_implementation()} {platform.python_version()}",
        "os": f"{platform.system()} {platform.release()} ({platform.platform()})",
    }


def load(variant: str) -> list[list[two_pass.TwoPassNote]]:
    stem = "opnd-m-noisy" if variant == "noisy" else "opnd-m"
    root = MEREDITH / stem / stem
    files = sorted(p for p in root.iterdir() if not p.name.startswith("."))
    return [meredith_notes(p.read_text(encoding="utf-8")) for p in files]


class _NullSpeller:
    """No-op speller: running the driver with it isolates the driver's own cost."""

    def __init__(self, look_ahead: bool) -> None:
        self.look_ahead = look_ahead

    def note_on(self, midi: int, t: Optional[float] = None, resolve_dir: int = 0, resolve_exact: int = 0,
                outward_partner: Optional[int] = None, outward_role: int = 0, local_key: object = None,
                degree_key: object = None) -> None:
        pass

    def note_off(self, midi: int, t: Optional[float] = None) -> None:
        pass

    def get_lof(self, midi: int) -> Optional[int]:
        return None


def time_mode(pieces: list[list[two_pass.TwoPassNote]], mode: str) -> dict[str, float]:
    if mode == "tp":
        passes = 0.0
        real = two_pass._drive_pass

        def timed(*a: Any, **k: Any) -> Any:
            nonlocal passes
            t0 = time.perf_counter()
            r = real(*a, **k)
            passes += time.perf_counter() - t0
            return r

        two_pass._drive_pass = timed
        try:
            t0 = time.perf_counter()
            for notes in pieces:
                spell_notes_lof(notes, "tp")
            total = time.perf_counter() - t0
        finally:
            two_pass._drive_pass = real
        return {"total": total, "passes": passes, "merge": total - passes}
    t0 = time.perf_counter()
    for notes in pieces:
        spell_notes_lof(notes, mode)  # type: ignore[arg-type]
    total = time.perf_counter() - t0
    t0 = time.perf_counter()
    for notes in pieces:
        drive_notes_lof(_NullSpeller(mode == "la"), notes, mode == "la")
    driver = time.perf_counter() - t0
    return {"total": total, "driver": driver, "speller": total - driver}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sets", default="clean,noisy")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--json", default=None, help="write results to this file")
    a = ap.parse_args()

    info = machine()
    print(f"machine: {info['cpu']}, {info['cores']} cores, {info['python']}, {info['os']}")
    flags = [f for f in ("-X dev" if sys.flags.dev_mode else "", "-O" if sys.flags.optimize else "") if f]
    if flags:
        print(f"flags: {' '.join(flags)}")
    results: dict[str, Any] = {"machine": info, "repeats": a.repeats, "sets": {}}
    for variant in [s for s in a.sets.split(",") if s]:
        t0 = time.perf_counter()
        pieces = load(variant)
        parse = time.perf_counter() - t0
        n_notes = sum(len(p) for p in pieces)
        print(f"\nMeredith {variant}: {len(pieces)} movements, {n_notes} notes, parse {parse:.2f} s")
        print(f"  {'mode':5s} {'total s':>8s} {'notes/s':>9s}   split (median of {a.repeats})")
        per: dict[str, Any] = {"movements": len(pieces), "notes": n_notes, "parse_s": parse, "modes": {}}
        for mode in [m for m in a.modes.split(",") if m]:
            runs = [time_mode(pieces, mode) for _ in range(a.repeats)]
            med = {k: statistics.median(r[k] for r in runs) for k in runs[0]}
            rate = n_notes / med["total"]
            split = ", ".join(f"{k} {v:.2f}" for k, v in med.items() if k != "total")
            print(f"  {mode:5s} {med['total']:8.2f} {rate:9.0f}   {split}  (runs: "
                  + " ".join(f"{r['total']:.2f}" for r in runs) + ")")
            per["modes"][mode] = {**med, "notes_per_s": rate, "runs_total": [r["total"] for r in runs]}
        results["sets"][variant] = per
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
