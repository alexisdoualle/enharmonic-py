"""Generate the seeded random MIDI event streams used as a parity input set.

The streams cover edge cases the corpora do not: big chords, unisons in a chord, repeated and
overlapping same-pitch notes, long rests, extreme registers (midi 0..127), zero-length notes, notes that
are never released, note-offs with no note-on, grace-note-short chromatics, spread (performed) chords
with float timestamps, and atonal / chromatic material.

The output (``parity/inputs/random.json``) is committed; the TypeScript exporter and the Python tests
both read it, so neither side reimplements this generator. Re-running with the same seeds rewrites the
same file (Python's ``random.Random`` is stable for these calls).

    python parity/gen_random.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent / "inputs" / "random.json"

MAJOR = [0, 2, 4, 5, 7, 9, 11]


def _sort(evs: list[list], rng: random.Random, shuffle_ties: bool) -> list[list]:
    # Default: at equal t, releases first, strikes bass-first (the fixture convention).
    if shuffle_ties:
        keyed = [(e[0], rng.random(), e) for e in evs]
        keyed.sort(key=lambda k: (k[0], k[1]))
        return [k[2] for k in keyed]
    return sorted(evs, key=lambda e: (e[0], 0 if e[1] == "off" else 1, e[2]))


def tonal(rng: random.Random, n_onsets: int, chroma: float, lo: int, hi: int) -> list[list]:
    """Diatonic material in a wandering key, with chromatic notes at rate ``chroma``."""
    evs: list[list] = []
    t = 0
    key = rng.randrange(12)
    for _ in range(n_onsets):
        if rng.random() < 0.03:
            key = (key + rng.choice([1, 2, 5, 7, 10, 11, 6])) % 12  # modulate
        size = rng.choice([1, 1, 1, 2, 3, 3, 4, 5])
        pitches = set()
        for _ in range(size):
            pc = (key + rng.choice(MAJOR)) % 12
            if rng.random() < chroma:
                pc = (pc + rng.choice([1, -1])) % 12
            octave = rng.randrange(lo // 12, hi // 12 + 1)
            m = 12 * octave + pc
            if lo <= m <= hi:
                pitches.add(m)
        dur = rng.choice([60, 125, 250, 250, 500, 500, 1000, 2000])
        for m in sorted(pitches):
            d = dur if rng.random() < 0.8 else rng.choice([40, 70, 3000])
            evs.append([t, "on", m])
            evs.append([t + d, "off", m])
        step = rng.choice([125, 250, 250, 500, 500, 1000])
        if rng.random() < 0.03:
            step += rng.choice([5000, 20000, 120000])  # long rest
        t += step
    return evs


def chaos(rng: random.Random, n_onsets: int) -> list[list]:
    """Uniform random pitches over the whole MIDI range, random chords and durations (incl. zero)."""
    evs: list[list] = []
    t = 0
    for _ in range(n_onsets):
        size = rng.choice([1, 1, 2, 3, 6, 10])
        for _ in range(size):
            m = rng.randrange(0, 128)
            d = rng.choice([0, 1, 50, 125, 400, 2500])
            evs.append([t, "on", m])
            evs.append([t + d, "off", m])
        t += rng.choice([0, 1, 125, 250, 1000, 30000])
    return evs


def repeats(rng: random.Random, n_onsets: int) -> list[list]:
    """Repeated pitches, unisons inside one chord, and overlapping same-pitch voices."""
    evs: list[list] = []
    t = 0
    pool = [rng.randrange(36, 96) for _ in range(5)]
    for _ in range(n_onsets):
        m = rng.choice(pool)
        kind = rng.random()
        if kind < 0.3:  # unison double at one instant
            evs += [[t, "on", m], [t, "on", m], [t + 300, "off", m], [t + 500, "off", m]]
        elif kind < 0.6:  # overlapping same-pitch voices
            evs += [[t, "on", m], [t + 100, "on", m], [t + 200, "off", m], [t + 600, "off", m]]
        else:  # plain re-strike chain
            evs += [[t, "on", m], [t + 120, "off", m]]
        if rng.random() < 0.3:
            m2 = rng.choice(pool) + rng.choice([1, -1, 3, 4, 7])
            evs += [[t, "on", m2], [t + 250, "off", m2]]
        t += rng.choice([125, 250, 250, 500])
    return evs


def spread(rng: random.Random, n_onsets: int) -> list[list]:
    """Performed chords: each note of a chord arrives a few ms (float) later; jittered releases."""
    evs: list[list] = []
    t = 0.0
    key = rng.randrange(12)
    for _ in range(n_onsets):
        size = rng.choice([1, 2, 3, 4])
        pcs = [(key + rng.choice(MAJOR) + (1 if rng.random() < 0.15 else 0)) % 12 for _ in range(size)]
        ms = sorted({12 * rng.randrange(3, 7) + pc for pc in pcs})
        for k, m in enumerate(ms):
            on = round(t + k * rng.uniform(0, 25), 3)
            evs.append([on, "on", m])
            evs.append([round(on + rng.uniform(30, 900), 3), "off", m])
        t += rng.choice([150.0, 300.0, 600.0]) + rng.uniform(-20, 20)
        if rng.random() < 0.02:
            key = (key + rng.choice([5, 7])) % 12
    return evs


def dangling(rng: random.Random, n_onsets: int) -> list[list]:
    """Notes never released, note-offs with no note-on, and shuffled tie order at equal t."""
    evs = tonal(rng, n_onsets, 0.2, 40, 90)
    out = []
    for e in evs:
        if e[1] == "off" and rng.random() < 0.08:
            continue  # never released
        out.append(e)
    for _ in range(n_onsets // 10):
        out.append([rng.randrange(0, 300000), "off", rng.randrange(30, 100)])  # stray release
    return out


def build() -> dict[str, list[list]]:
    streams: dict[str, list[list]] = {}
    for k in range(10):
        rng = random.Random(1000 + k)
        lo, hi = (0, 127) if k % 3 == 0 else (21, 108)
        streams[f"tonal_{k:02d}"] = _sort(tonal(rng, 600, 0.05 + 0.04 * k, lo, hi), rng, False)
    for k in range(6):
        rng = random.Random(2000 + k)
        streams[f"chaos_{k:02d}"] = _sort(chaos(rng, 400), rng, k % 2 == 1)
    for k in range(6):
        rng = random.Random(3000 + k)
        streams[f"repeats_{k:02d}"] = _sort(repeats(rng, 400), rng, k % 2 == 1)
    for k in range(6):
        rng = random.Random(4000 + k)
        streams[f"spread_{k:02d}"] = _sort(spread(rng, 500), rng, False)
    for k in range(4):
        rng = random.Random(5000 + k)
        streams[f"dangling_{k:02d}"] = _sort(dangling(rng, 400), rng, True)
    # Tiny hand-made streams: empty, a single note, a single unreleased note.
    streams["tiny_empty"] = []
    streams["tiny_single"] = [[0, "on", 61], [500, "off", 61]]
    streams["tiny_unreleased"] = [[0, "on", 61], [0, "on", 65], [500, "off", 65]]
    streams["tiny_zero_len"] = [[0, "on", 61], [0, "off", 61], [0, "on", 61], [10, "off", 61], [10, "on", 62], [20, "off", 62]]
    return streams


def main() -> None:
    streams = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        f.write("{\n")
        items = list(streams.items())
        for i, (k, v) in enumerate(items):
            f.write(f" {json.dumps(k)}: {json.dumps(v, separators=(',', ':'))}{',' if i < len(items) - 1 else ''}\n")
        f.write("}\n")
    n = sum(sum(1 for e in v if e[1] == "on") for v in streams.values())
    print(f"wrote {OUT} ({len(streams)} streams, {n} note-ons)")


if __name__ == "__main__":
    main()
