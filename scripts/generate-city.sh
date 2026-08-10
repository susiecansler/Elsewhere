#!/bin/bash
# Generate every direction between one new city and the cities already in the
# corpus. Costs roughly $1.50 per direction — see the printed estimate before
# it submits anything.
#
#   bash scripts/generate-city.sh new_york
#   bash scripts/generate-city.sh new_york austin        # one pair only, ~$3
#
set -euo pipefail
CITY="${1:?usage: generate-city.sh <new_city> [only_partner]}"
ONLY="${2:-}"
cd "$(dirname "$0")/../pipeline"
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/elsewhere}"

PARTNERS=(austin chapel_hill chicago los_angeles portland tokyo)
[ -n "$ONLY" ] && PARTNERS=("$ONLY")

echo "== preflight: one live request, proves the balance and the schema"
uv run elsewhere generate preflight --from "$CITY" --to "${PARTNERS[0]}" --live

for p in "${PARTNERS[@]}"; do
  [ "$p" = "$CITY" ] && continue
  for pair in "$CITY:$p" "$p:$CITY"; do
    src="${pair%%:*}"; dst="${pair##*:}"
    echo "== submit $src -> $dst"
    uv run elsewhere generate submit --from "$src" --to "$dst"
  done
done

cat <<'NEXT'

Submitted. Batches take up to a few hours. Then, for each pair:
  elsewhere generate collect --from <src> --to <dst>
  elsewhere verify  --from <src> --to <dst>
Finally, once:
  elsewhere links
NEXT
