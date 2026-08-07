# Elsewhere — MVP Implementation Plan

## What the MVP is

One city pair — **Austin ↔ Chicago** — with ~120 named institutions per side,
matched, verified, and **scored against real ground truth**. Queryable from a
CLI.

The MVP exists to answer one question: *does role-based matching actually beat
category matching?* Everything here serves that. No web app, no second city
pair, no long-tail places until the numbers say the model works.

### Success criteria

Measured against a hand-cleaned ground-truth set mined from local subreddits:

| Metric | Target |
| --- | --- |
| Top-1 match agrees with local consensus | ≥ 60% |
| Top-3 contains the consensus answer | ≥ 85% |
| Hallucinated / closed businesses in output | < 2% |

If top-1 lands below 60%, the fix is almost certainly **the role taxonomy**,
not the model or the prompt. Budget for two or three taxonomy revisions.

### Explicitly not in the MVP

Web UI · additional city pairs · long-tail (non-institution) places · live
Google Places verification · user accounts · anything hosted.

---

## Stack

**Python** for the entire MVP — the work is ~80% data pipeline. TypeScript
enters later for the web app; the repo is laid out so it can slot in without
rearranging anything.

```
Elsewhere/
├── pipeline/                  # Python — all MVP work lives here
│   ├── elsewhere/
│   │   ├── models.py          # Pydantic: Place, RoleTag, Candidate, Match
│   │   ├── taxonomy.py        # loads + validates the role vocabulary
│   │   ├── seeds.py           # seed corpus build + FSQ join
│   │   ├── generate.py        # Batch API match generation
│   │   ├── verify.py          # existence / liveness checks
│   │   ├── evaluate.py        # scoring against ground truth
│   │   ├── resolve.py         # query → canonical place
│   │   └── cli.py
│   ├── tests/
│   └── pyproject.toml
├── data/
│   ├── taxonomy/roles.yaml    # the core artifact
│   ├── seeds/                 # austin.jsonl, chicago.jsonl
│   ├── places/                # DuckDB over Foursquare OS Places
│   ├── matches/               # generated + verified output
│   └── eval/                  # ground_truth.jsonl, scores
├── web/                       # (post-MVP, TypeScript)
└── docs/
```

Dependencies: `uv` for env/deps, `anthropic`, `pydantic`, `duckdb`,
`sentence-transformers` (Phase 5 only), `pytest`.

---

## Phases

### Phase 0 — Scaffolding · ~½ day

Project skeleton and the role taxonomy, which is the single most important
artifact in the repo.

- `pipeline/` package, `uv` lockfile, `.env.example` (`ANTHROPIC_API_KEY`)
- Pydantic models for `Place`, `RoleTag`, `Candidate`, `Match`
- **`data/taxonomy/roles.yaml`** — a *fixed* vocabulary of ~40–60 roles, each
  with a name, a one-line definition, and 2–3 exemplars. Fixed rather than
  free-form so a bad match is debuggable; exemplars because they steer the
  model far better than definitions alone.

  ```yaml
  - id: regional_grocery_cult
    definition: >
      Regional grocery chain that locals are actively loyal to and
      identify with, not merely the nearest option.
    exemplars: ["H-E-B (Austin)", "Wegmans (Rochester)", "Publix (Atlanta)"]
  ```

**Done when:** `uv run elsewhere --help` runs; the taxonomy loads and fails
loudly on a malformed entry.

---

### Phase 1 — Seed corpus · ~1 day

Two lists of named institutions, plus the structured substrate for verifying
them.

- Hand-curate **~120 Austin** institutions spanning grocery, gym, tacos,
  coffee, BBQ, dive bar, hardware, pharmacy, breakfast, music venue, swimming
  hole, department store. These are the *queries*.
- Hand-curate **~120 Chicago** institutions the same way. These are the
  *candidate pool* — they make the model's job tractable and give verification
  something to check against.
- Download Foursquare OS Places (Apache 2.0, free), filter to the two metro
  bounding boxes, load into DuckDB at `data/places/`.
- Join each seed to an FSQ place ID where one exists.

**Done when:** `elsewhere seeds build` writes `austin.jsonl` and
`chicago.jsonl`; every seed carries an FSQ ID or an explicit `unmatched` flag.

---

### Phase 2 — Match generation · ~1 day

The batch pipeline. For each Austin seed: role tags, price tier,
local-vs-national, and three ranked Chicago candidates with reasoning.

- **Batch API** (`client.messages.batches.create`) — 50% off, and this is
  entirely latency-insensitive.
- **Prompt caching** on the taxonomy prefix — cache reads are ~0.1× input
  price, and the taxonomy clears Opus 5's 512-token minimum comfortably.
- **Structured outputs** so nothing needs parsing and nothing fails halfway
  through a 120-call batch.
- Model: `claude-opus-5`. The depth of regional knowledge *is* the product.

Estimated cost: **~$25** for the full corpus.

**Done when:** `elsewhere generate --from austin --to chicago` writes
`data/matches/austin-chicago.raw.jsonl`, batch reports zero errored requests,
and every record validates against the schema.

---

### Phase 3 — Verification · ~½ day

Kill hallucinations and closed businesses before they reach the eval.

- Cross-check every candidate against the DuckDB place table: does it exist,
  is it operating, is its category plausible for the assigned role?
- Write failures to `rejects.jsonl` **with a reason** — this file is a
  debugging tool, read it rather than just counting it.
- Treat verification as a **flag, not a hard filter**. FSQ coverage of small
  local businesses is uneven; auto-deleting on a miss will silently discard
  good matches.

**Done when:** every candidate is either verified or in `rejects.jsonl` with a
reason, and the hallucination rate is measured and written to the run summary.

---

### Phase 4 — Eval harness · ~1–2 days

**The most important phase.** Without this you have opinions, not results.

- Mine local subreddits (r/austin, r/chicago, r/AskChicago) for the literal
  phrasings people use: *"Chicago equivalent of"*, *"Chicago version of"*,
  *"our answer to"*, *"the X of Y"*.
- Hand-clean into `data/eval/ground_truth.jsonl` — target **60–100 pairs**.
  Where locals disagree, record multiple acceptable answers; disagreement is
  signal, not noise.
- Scorer computing top-1 and top-3 accuracy.

**Done when:** `elsewhere eval` prints top-1/top-3 against ground truth, and
the baseline numbers are committed to the repo so taxonomy revisions can be
measured against them.

---

### Phase 5 — Query CLI · ~½ day

- Entity resolution: normalize, then fuzzy + embedding match against seed
  names and aliases (local `sentence-transformers` — free, fast, no API call).
- `elsewhere ask "HEB" --from austin --to chicago` → match, alternates, and
  the reasoning.

**Done when:** `"HEB"`, `"H-E-B"`, and `"heb grocery"` all resolve to the same
seed and return the match with its explanation.

---

## Sequencing

Phases 0–3 are strictly sequential. **Phase 4 can and should start in
parallel with Phase 1** — the ground-truth mine is independent of generation,
it's the slowest phase, and having it ready the moment Phase 3 finishes is
what makes the taxonomy iteration loop fast.

Rough total: **4–6 days** to a scored corpus.

---

## Risks

| Risk | Mitigation |
| --- | --- |
| Role taxonomy too coarse or too fine | The main iteration loop. Phase 4 exists to detect this; budget 2–3 revisions |
| FSQ coverage thin for small local businesses | Verification flags rather than filters (Phase 3) |
| Reddit API volume limits or cost | Cap the mine; hand-label to fill gaps to 60 pairs |
| Chicago candidate pool too narrow, capping recall | Widen the Phase 1 Chicago list before touching the taxonomy — check this first when top-3 underperforms |
| Model knows Austin far better than Chicago (or vice versa) | Score both directions; asymmetry points at seed coverage, not the model |

---

## After the MVP

In rough priority order, gated on the eval numbers:

1. Second and third city pairs — the first real test of whether the taxonomy generalizes
2. HTTP lookup API
3. TypeScript web app in `web/`
4. Long-tail places (the genuinely hard regime)
