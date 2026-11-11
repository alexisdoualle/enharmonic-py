#!/usr/bin/env bash
# Re-export goldens from a prod commit and show divergence immediately.
#
#   parity/sync.sh [REV] [--update]
#
# 1. exports REV (default: main of $ENHARMONIC_PROD) into parity/out/<sha>/ (gitignored);
# 2. diffs it against the committed parity/goldens/ -> did PROD's behaviour change since the port's commit?
# 3. replays the fresh goldens through the Python port   -> does the PORT still match the new prod?
# With --update, the fresh export replaces parity/goldens/ (commit it together with the matching port change).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
REV="main"; UPDATE=0
for a in "$@"; do case "$a" in --update) UPDATE=1 ;; *) REV="$a" ;; esac; done
PROD="${ENHARMONIC_PROD:-$HOME/JavaScript/enharmonic}"
SHA="$(git -C "$PROD" rev-parse --verify "$REV^{commit}")" || exit 1
OUT="$HERE/out/$SHA"
PY="${PYTHON:-$ROOT/.venv/bin/python}"; [ -x "$PY" ] || PY=python3

"$HERE/export.sh" "$SHA" "$OUT" || exit 1
echo; echo "== prod behaviour: committed goldens vs $REV ($SHA) =="
"$PY" "$HERE/diff_goldens.py" "$OUT" "$HERE/goldens"; PROD_CHANGED=$?
echo; echo "== Python port vs $REV ($SHA) =="
"$PY" "$HERE/check.py" --goldens "$OUT"; PORT_BAD=$?

if [ "$UPDATE" = 1 ]; then
    if [ "$PORT_BAD" = 0 ]; then cp "$OUT"/*.json.gz "$HERE/goldens/" && echo "goldens updated to $SHA"
    else echo "NOT updating goldens: the port does not match $SHA yet"; fi
fi
echo; echo "summary: prod changed since goldens: $([ $PROD_CHANGED = 0 ] && echo no || echo YES); port matches $REV: $([ $PORT_BAD = 0 ] && echo yes || echo NO)"
exit $(( PORT_BAD != 0 ))
