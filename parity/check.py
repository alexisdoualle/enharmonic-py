"""Parity report: replay every golden input through the Python port and count mismatches per set and mode.

    python parity/check.py                       # all sets present in parity/goldens
    python parity/check.py --sets random,fixtures
    python parity/check.py --goldens <dir>       # check against another export (used by sync.sh)

Exit status 1 if any note differs or an input changed since the goldens were exported.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from harness import GOLDENS, SETS, ModeResult, compare, input_problems, iter_set, load_golden  # noqa: E402


def run(sets: list[str], goldens: Path, verbose: bool) -> int:
    bad = 0
    rows: list[ModeResult] = []
    for name in sets:
        path = goldens / f"{name}.json.gz"
        if not path.exists():
            print(f"(no {path.name}; skipped)")
            continue
        golden = load_golden(name, goldens)
        print(f"{name}: goldens from prod {golden['source']['commit'][:12]}")
        probs = input_problems(name, golden)
        for p in probs:
            print(f"  INPUT: {p}")
        bad += bool(probs)
        results: dict[str, ModeResult] = {}
        for mode, iid, thunk, want in iter_set(name, golden):
            r = results.setdefault(mode, ModeResult(name, mode))
            t0 = time.perf_counter()
            got = thunk()
            r.seconds += time.perf_counter() - t0
            mism = compare(got, want)
            r.inputs += 1
            r.notes += len(want)
            r.mismatches += len(mism)
            if mism and len(r.first) < 5:
                i = mism[0]
                r.first.append(f"{iid} note {i}: python {got[i] if i < len(got) else '∅'} vs ts {want[i] if i < len(want) else '∅'}"
                               f" ({len(mism)} differ)")
        for r in results.values():
            rows.append(r)
            flag = "OK " if r.mismatches == 0 else "BAD"
            print(f"  {flag} {r.mode:12s} inputs {r.inputs:4d}  notes {r.notes:8d}  mismatches {r.mismatches:6d}  {r.seconds:7.1f}s")
            if verbose or r.mismatches:
                for line in r.first:
                    print(f"        {line}")
            bad += r.mismatches > 0
    total = sum(r.notes for r in rows)
    mism = sum(r.mismatches for r in rows)
    print(f"\nTOTAL: {total} notes compared, {mism} mismatches")
    return 1 if bad else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default=",".join(SETS))
    ap.add_argument("--goldens", default=str(GOLDENS))
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    sys.exit(run([s for s in a.sets.split(",") if s], Path(a.goldens), a.verbose))


if __name__ == "__main__":
    main()
