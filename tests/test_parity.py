"""Note-for-note parity with the TypeScript speller (prod), per input set and mode.

Goldens: ``parity/goldens/*.json.gz`` (exported by ``parity/export.sh``). Each test replays one mode over
every input of one set and requires ZERO differing notes. Fixture and Meredith inputs are read from their
home directories (see README) and must hash-match the export; missing inputs skip the test.

Meredith runs take ~1-2 minutes per mode in pure Python; deselect with ``-m "not slow"``.
"""

import pytest

from harness import GOLDENS, compare, input_problems, iter_set, load_golden

_CASES = []
for _set in ("random", "fixtures", "meredith-clean", "meredith-noisy"):
    if (GOLDENS / f"{_set}.json.gz").exists():
        _g = load_golden(_set)
        marks = [pytest.mark.slow] if _set.startswith("meredith") else []
        for _mode in _g["modes"]:
            _CASES.append(pytest.param(_set, _mode, id=f"{_set}-{_mode}", marks=marks))

_CACHE: dict = {}


def _golden(name):
    if name not in _CACHE:
        _CACHE[name] = load_golden(name)
    return _CACHE[name]


@pytest.mark.parametrize("set_name,mode", _CASES)
def test_parity(set_name, mode):
    g = _golden(set_name)
    probs = input_problems(set_name, g)
    missing = [p for p in probs if p.startswith("missing")]
    if missing:
        pytest.skip(f"{len(missing)} inputs missing, e.g. {missing[0]}")
    assert not probs, "inputs changed since the goldens were exported (re-export): " + "; ".join(probs[:3])
    notes = 0
    failures = []
    for m, iid, thunk, want in iter_set(set_name, g):
        if m != mode:
            continue
        got = thunk()
        notes += len(want)
        bad = compare(got, want)
        if bad:
            i = bad[0]
            failures.append(f"{iid}: {len(bad)} notes differ, first at {i}: "
                            f"python {got[i] if i < len(got) else None} vs ts {want[i] if i < len(want) else None}")
    assert notes > 0
    assert not failures, f"{set_name}/{mode}: " + "\n".join(failures[:10])
