#!/usr/bin/env bash
# Export parity goldens from a prod `enharmonic` commit, WITHOUT touching the prod working tree:
# the commit's src/ and test/eval/ are extracted with `git archive` into a scratch dir, then run with
# prod's own tsx (node_modules of the prod checkout).
#
#   parity/export.sh [REV] [OUT_DIR]            # defaults: REV=main, OUT_DIR=parity/goldens
#
# Environment:
#   ENHARMONIC_PROD      prod checkout (default ~/JavaScript/enharmonic)
#   ENHARMONIC_LAB_FIXTURES  lab fixtures dir (default ~/JavaScript/enharmonic-lab/fixtures)
#   MEREDITH_CORPUS      Meredith corpus root holding opnd-m/opnd-m and opnd-m-noisy/opnd-m-noisy
#                        (default $ENHARMONIC_PROD/corpora/meredith)
#   PARITY_SETS          comma list of sets (default pure,random,fixtures,meredith,traces)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROD="${ENHARMONIC_PROD:-$HOME/JavaScript/enharmonic}"
REV="${1:-main}"
OUT="${2:-$HERE/goldens}"
LAB="${ENHARMONIC_LAB_FIXTURES:-$HOME/JavaScript/enharmonic-lab/fixtures}"
MER="${MEREDITH_CORPUS:-$PROD/corpora/meredith}"
SETS="${PARITY_SETS:-pure,random,fixtures,meredith,traces}"

SHA="$(git -C "$PROD" rev-parse --verify "$REV^{commit}")"
TSX="$PROD/node_modules/.bin/tsx"
[ -x "$TSX" ] || { echo "tsx not found at $TSX (run npm install in $PROD)"; exit 1; }

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/enharmonic-parity.XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT
git -C "$PROD" archive "$SHA" package.json src test/eval | tar -x -C "$SCRATCH"
echo "prod $REV = $SHA (extracted to $SCRATCH)"

mkdir -p "$OUT"
"$TSX" "$HERE/export.mts" --src "$SCRATCH" --commit "$SHA" --out "$OUT" \
    --lab-fixtures "$LAB" --meredith "$MER" --sets "$SETS"
