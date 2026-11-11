"""JavaScript number semantics the port must reproduce exactly.

Python and JavaScript disagree on a few primitives the speller relies on. Every call site in the port goes
through these helpers so each deviation is handled once and visibly:

- ``%``: JavaScript keeps the sign of the dividend (``-1 % 12 == -1``), Python the sign of the divisor
  (``-1 % 12 == 11``). The TypeScript code almost always wraps it as ``((x % n) + n) % n``, which equals
  Python's ``x % n`` for a positive ``n``. Bare ``%`` on a possibly negative value uses :func:`js_mod`.
- ``Math.round``: rounds half toward +infinity (``Math.round(-2.5) == -2``); Python's ``round`` is banker's.
- ``Math.sign``.
"""

from __future__ import annotations

import math


def js_mod(a: int, n: int) -> int:
    """JavaScript ``a % n`` for integers (result takes the sign of the dividend)."""
    r = abs(a) % abs(n)
    return -r if a < 0 else r


def js_round(x: float) -> int:
    """JavaScript ``Math.round``: nearest integer, ties toward +infinity."""
    f = math.floor(x)
    return int(f) + 1 if x - f >= 0.5 else int(f)


def js_sign(x: float) -> int:
    """JavaScript ``Math.sign`` for a finite number (0 for 0)."""
    return 1 if x > 0 else -1 if x < 0 else 0
