#!/bin/bash
# Generate every Los Angeles direction, plus the Tokyo places that hit the
# credit wall. Run after topping up API credits.
#
#   bash scripts/generate-los-angeles.sh
#
# Roughly $12 of batch-discounted generation for LA (8 directions), plus
# about $0.70 to fill the Tokyo gaps. Collect is resumable: re-running only
# fills what's missing.
set -euo pipefail
cd "$(dirname "$0")/../pipeline"
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/elsewhere}"

PAIRS=(
  los_angeles:austin austin:los_angeles
  los_angeles:chicago chicago:los_angeles
  los_angeles:portland portland:los_angeles
  los_angeles:tokyo tokyo:los_angeles
  # The six Tokyo directions had ~50 requests fail on an exhausted balance.
  austin:tokyo tokyo:austin chicago:tokyo tokyo:chicago portland:tokyo tokyo:portland
)

echo "== preflight (one live request, proves the balance) =="
uv run elsewhere generate preflight --from los_angeles --to austin --live

for pair in "${PAIRS[@]}"; do
  src="${pair%%:*}"; dst="${pair##*:}"
  echo "== submit $src -> $dst"
  uv run elsewhere generate submit --from "$src" --to "$dst"
done

echo
echo "Batches submitted. They take up to a few hours. Then:"
echo "  for p in ${PAIRS[*]}; do ... elsewhere generate collect --from \${p%%:*} --to \${p##*:}; done"
echo "  elsewhere verify --from <src> --to <dst>   # per pair"
echo "  elsewhere links                            # refresh websites + coordinates"
