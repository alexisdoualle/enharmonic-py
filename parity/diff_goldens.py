"""Diff two golden exports (e.g. a fresh prod export vs the committed goldens), per set and mode.

    python parity/diff_goldens.py <new_dir> [<old_dir>=parity/goldens]

Reports, per set and mode, how many notes changed spelling between the two prod commits, and whether the
inputs (hashes) or source commit differ. Exit 1 if any spelling differs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from harness import GOLDENS, load_golden  # noqa: E402

NAMES = ("pure", "random", "fixtures", "meredith-clean", "meredith-noisy", "traces")
IGNORED_KEYS = {"timingMs", "source", "root"}


def main() -> None:
    new = Path(sys.argv[1])
    old = Path(sys.argv[2]) if len(sys.argv) > 2 else GOLDENS
    changed = 0
    for name in NAMES:
        a, b = old / f"{name}.json.gz", new / f"{name}.json.gz"
        if not a.exists() or not b.exists():
            print(f"{name}: only in {'new' if b.exists() else 'old' if a.exists() else 'neither'}")
            continue
        ga, gb = load_golden(name, old), load_golden(name, new)
        print(f"{name}: {ga['source']['commit'][:12]} -> {gb['source']['commit'][:12]}")
        if "modes" not in ga:
            same = {k: v for k, v in ga.items() if k not in IGNORED_KEYS} == {k: v for k, v in gb.items() if k not in IGNORED_KEYS}
            print(f"  {'identical' if same else 'DIFFERS'}")
            changed += not same
            continue
        if ga.get("inputs") != gb.get("inputs") or ga.get("inputSha256") != gb.get("inputSha256"):
            print("  NOTE: input hashes differ between the two exports")
        for mode in sorted(set(ga["modes"]) | set(gb["modes"])):
            pa, pb = ga["modes"].get(mode, {}), gb["modes"].get(mode, {})
            notes = diff = 0
            pieces = []
            for iid in sorted(set(pa) | set(pb)):
                va, vb = pa.get(iid, []), pb.get(iid, [])
                notes += max(len(va), len(vb))
                d = sum(1 for x, y in zip(va, vb) if x != y) + abs(len(va) - len(vb))
                if d:
                    pieces.append(f"{iid} ({d})")
                diff += d
            changed += diff > 0
            print(f"  {'same   ' if diff == 0 else 'CHANGED'} {mode:12s} {notes:8d} notes, {diff:6d} changed"
                  + (f"  e.g. {', '.join(pieces[:4])}" if pieces else ""))
    sys.exit(1 if changed else 0)


if __name__ == "__main__":
    main()
