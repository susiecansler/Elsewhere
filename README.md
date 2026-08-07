# Elsewhere

Find the parallel places in an unfamiliar city.

You know your city by its places, not its categories. "Grocery store" isn't
what you want in Chicago — you want *your* H-E-B. Elsewhere maps a place you
love in one city to its closest equivalent somewhere else.

## Example

> I'm from Austin, traveling to Chicago.

| Austin | Chicago equivalent |
| --- | --- |
| H-E-B | Mariano's |
| Lifetime Fitness | East Bank Club |
| Torchy's Tacos | Big Star |

## The core problem

A good match isn't the nearest category match — it's the place that occupies
the same *role* for locals. Dimensions that matter:

- **Category** — grocery, gym, taco joint
- **Price tier** — what it costs relative to local alternatives
- **Cultural role** — regional icon, weekend ritual, late-night default
- **Local vs. national** — a chain that exists in both cities is a boring answer
- **Vibe** — who goes there, and why they're loyal to it

## Status

Planning. Nothing built yet — the matching model and data strategy are
settled, implementation starts next.

- [MVP implementation plan](docs/mvp-plan.md) — phases, success criteria, risks

## Approach in brief

- **Place data:** Overture Maps (CDLA-Permissive) as the stored substrate.
  Foursquare OS Places was the original pick but has moved behind a token;
  Google Places can't be a database at all — only `place_id` is storable
  indefinitely.
- **Matching:** place → *role* → place, against a fixed role vocabulary. This
  is what rules out "H-E-B → Jewel-Osco": both are groceries, only one carries
  the loyalty.
- **Generation:** an LLM proposes matches and reasoning offline in batch; the
  places dataset verifies they exist. Serving is a lookup, not a model call.
- **Ground truth:** mined from local subreddits, where people ask this exact
  question unprompted.

## Running it

```bash
cd pipeline && make setup && make places && make seeds
```

`make help` lists the rest. The virtualenv is created **outside** the repo
(`~/.venvs/elsewhere`) because this checkout lives under `~/Documents`, which
iCloud Drive syncs — iCloud leaves conflict copies inside `.venv` that break
the editable install with a confusing `No module named 'elsewhere'`. Override
`UV_PROJECT_ENVIRONMENT` if your checkout isn't in a synced folder.

## MVP scope

Austin ↔ Chicago, ~120 named institutions per side, scored against ground
truth, queryable from a CLI. Target: top-1 ≥ 60%, top-3 ≥ 85%.

Web app comes after the numbers say the matching model works.
