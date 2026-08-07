"""Match generation via the Batch API.

Offline and latency-insensitive by design: the serving path is a lookup, not a
model call, so everything expensive happens here once per city pair.

Three cost levers, all load-bearing at this scale:
  * Batch API — 50% off, and nothing here is waiting on a user.
  * Prompt caching — the system prompt (instructions + taxonomy + candidate
    pool) is ~3k stable tokens reused across every request in the batch.
  * Structured outputs — no parse failures partway through a 117-call job.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from anthropic import Anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from pydantic import BaseModel, Field

from elsewhere import places, seeds, taxonomy
from elsewhere.models import Candidate, Match, Place, PriceTier, Reach
from elsewhere.taxonomy import REPO_ROOT

MATCHES_DIR = REPO_ROOT / "data" / "matches"

MODEL = "claude-opus-5"
MAX_TOKENS = 8000
#: Sweep this before accepting the corpus. Opus 5 is unusually strong at
#: low/medium, and effort defaults carried over from other models rarely
#: transfer.
EFFORT = "high"

N_CANDIDATES = 3


# ─── Structured output schema ─────────────────────────────────────────────


class GeneratedCandidate(BaseModel):
    name: str = Field(description="The place's common name, as locals write it")
    reasoning: str = Field(
        description=(
            "2-3 sentences on why this fills the same role. Name the specific "
            "shared quality, not the category. Shown to the user."
        )
    )
    confidence: float = Field(ge=0.0, le=1.0)


class GeneratedMatch(BaseModel):
    role_tags: list[str] = Field(
        description="1-3 role ids from the vocabulary, most defining first"
    )
    price_tier: Literal[1, 2, 3, 4]
    reach: Literal["local", "regional", "national"]
    candidates: list[GeneratedCandidate] = Field(description=f"Exactly {N_CANDIDATES}, best first")


# ─── Prompt ───────────────────────────────────────────────────────────────

INSTRUCTIONS = """\
You match places between cities by the ROLE they play in local life, not by \
category. This distinction is the whole task.

The failure mode to avoid: H-E-B and Jewel-Osco are both grocery stores in \
the same price bracket with the same city-wide footprint, and Jewel-Osco is \
still the wrong answer. H-E-B is the store Texans are actively loyal to; \
Jewel-Osco is where Chicagoans shop because it is there. The right answer is \
Mariano's, which occupies the role. Match the loyalty, the ritual, and the \
place in people's mental map — not the shelf contents.

Rules:
- A chain present in BOTH cities is almost always a boring answer. If Austin \
  and Chicago both have it, the reader already knows about it. Prefer a local \
  or regional counterpart unless nothing else fills the role.
- Reasoning must name the specific shared quality. "Both are gyms" is \
  useless; "both are the club where deals get done, with a waitlist and a \
  membership that signals something" is the answer.
- Rank honestly. If the second candidate is nearly as good, say so in its \
  reasoning rather than inflating the first one's confidence.
- If no good equivalent exists, say so in the reasoning and give low \
  confidence. A city genuinely lacking a counterpart is a real finding — do \
  not manufacture one.

Assign 1-3 role tags from the vocabulary below, most defining first. Use only \
ids from this list.

Price tier is RELATIVE to local alternatives in the same category, not \
absolute: 1 budget, 2 moderate, 3 upscale, 4 luxury.

Reach describes the source place: local (one metro), regional (multi-city, \
identified with a region), national.
"""


def build_system_prompt(target_city: str) -> str:
    """Assemble the cached prefix.

    Must be byte-stable across every request in a batch — any variation here
    costs the cache on all of them. Ordering is fixed by `as_prompt_block`
    and by sorting the candidate pool.
    """
    pool = seeds.load_seeds(target_city)
    names = sorted(p.name for p in pool)
    return (
        f"{INSTRUCTIONS}\n"
        f"\n# Role vocabulary\n\n{taxonomy.as_prompt_block()}\n"
        f"\n# Known {target_city.title()} places\n\n"
        f"These are well-known {target_city.title()} institutions, offered as "
        f"grounding. You are NOT restricted to this list — name a better "
        f"answer if one exists.\n\n" + "\n".join(f"- {n}" for n in names) + "\n"
    )


def build_user_prompt(place: Place, target_city: str) -> str:
    aliases = f" (also called: {', '.join(place.aliases)})" if place.aliases else ""
    return (
        f"Source place: {place.name}{aliases}\n"
        f"Source city: {place.city.title()}\n"
        f"Category: {place.category}\n\n"
        f"What is the {target_city.title()} equivalent?"
    )


def slug(name: str) -> str:
    """custom_id for a request. Batch results arrive in arbitrary order."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s[:60] or "unnamed"


# ─── Batch lifecycle ──────────────────────────────────────────────────────


def build_requests(source_city: str, target_city: str) -> list[Request]:
    system = build_system_prompt(target_city)
    corpus = seeds.load_seeds(source_city)

    return [
        Request(
            custom_id=slug(place.name),
            params=MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                output_config={
                    "effort": EFFORT,
                    "format": {
                        "type": "json_schema",
                        "schema": GeneratedMatch.model_json_schema(),
                    },
                },
                messages=[{"role": "user", "content": build_user_prompt(place, target_city)}],
            ),
        )
        for place in corpus
    ]


def submit(source_city: str, target_city: str, client: Anthropic | None = None) -> str:
    """Create the batch and return its id."""
    client = client or Anthropic()
    batch = client.messages.batches.create(requests=build_requests(source_city, target_city))
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    state_path(source_city, target_city).write_text(
        json.dumps({"batch_id": batch.id, "source": source_city, "target": target_city})
    )
    return batch.id


def state_path(source_city: str, target_city: str) -> Path:
    return MATCHES_DIR / f"{source_city}-{target_city}.batch.json"


def load_batch_id(source_city: str, target_city: str) -> str | None:
    path = state_path(source_city, target_city)
    if not path.exists():
        return None
    return json.loads(path.read_text())["batch_id"]


def status(batch_id: str, client: Anthropic | None = None):
    client = client or Anthropic()
    return client.messages.batches.retrieve(batch_id)


def collect(
    source_city: str,
    target_city: str,
    batch_id: str,
    client: Anthropic | None = None,
) -> tuple[list[Match], list[dict]]:
    """Fetch results and assemble Match records.

    Returns (matches, failures). Results come back in arbitrary order, so
    everything is keyed by custom_id rather than position.
    """
    client = client or Anthropic()
    corpus = {slug(p.name): p for p in seeds.load_seeds(source_city)}
    known_roles = taxonomy.role_ids()

    matches: list[Match] = []
    failures: list[dict] = []

    for result in client.messages.batches.results(batch_id):
        place = corpus.get(result.custom_id)
        if place is None:
            failures.append({"custom_id": result.custom_id, "error": "unknown custom_id"})
            continue

        if result.result.type != "succeeded":
            failures.append(
                {
                    "custom_id": result.custom_id,
                    "name": place.name,
                    "error": result.result.type,
                }
            )
            continue

        text = next((b.text for b in result.result.message.content if b.type == "text"), None)
        if text is None:
            failures.append(
                {"custom_id": result.custom_id, "name": place.name, "error": "no text block"}
            )
            continue

        try:
            gen = GeneratedMatch.model_validate_json(text)
        except Exception as exc:
            failures.append(
                {
                    "custom_id": result.custom_id,
                    "name": place.name,
                    "error": f"schema: {exc}",
                }
            )
            continue

        # Structured output constrains shape, not vocabulary — a role id can
        # still be invented, or go stale if the taxonomy was edited after
        # generation. Drop unknown ids rather than poisoning the corpus.
        tags = [t for t in gen.role_tags if t in known_roles]
        if dropped := [t for t in gen.role_tags if t not in known_roles]:
            failures.append(
                {
                    "custom_id": result.custom_id,
                    "name": place.name,
                    "error": f"unknown role tags dropped: {dropped}",
                    "recoverable": True,
                }
            )

        matches.append(
            Match(
                source=place,
                target_city=target_city,
                role_tags=tags,
                price_tier=PriceTier(gen.price_tier),
                reach=Reach(gen.reach),
                candidates=[
                    Candidate(name=c.name, reasoning=c.reasoning, confidence=c.confidence)
                    for c in gen.candidates
                ],
            )
        )

    matches.sort(key=lambda m: m.source.name)
    return matches, failures


def raw_path(source_city: str, target_city: str) -> Path:
    return MATCHES_DIR / f"{source_city}-{target_city}.raw.jsonl"


def write_matches(matches: list[Match], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for m in matches:
            f.write(m.model_dump_json() + "\n")


def read_matches(path: Path) -> list[Match]:
    if not path.exists():
        raise FileNotFoundError(f"no matches at {path}")
    with path.open() as f:
        return [Match.model_validate_json(line) for line in f if line.strip()]


def estimate_cost(source_city: str, target_city: str) -> dict[str, float]:
    """Rough pre-flight cost estimate, so nobody submits blind.

    Uses the real token counter on the actual prompts rather than a guess.
    Opus 5 is $5/$25 per MTok; batch halves both. Output is estimated, not
    counted — it hasn't been generated yet.
    """
    client = Anthropic()
    system = build_system_prompt(target_city)
    corpus = seeds.load_seeds(source_city)

    sample = client.messages.count_tokens(
        model=MODEL,
        system=[{"type": "text", "text": system}],
        messages=[{"role": "user", "content": build_user_prompt(corpus[0], target_city)}],
    ).input_tokens

    n = len(corpus)
    # First request writes the cache at 1.25x; the rest read it at ~0.1x.
    system_tokens = client.messages.count_tokens(
        model=MODEL,
        messages=[{"role": "user", "content": system}],
    ).input_tokens
    per_request_uncached = max(sample - system_tokens, 0)

    input_cost = (
        (system_tokens * 1.25 + system_tokens * 0.1 * (n - 1) + per_request_uncached * n)
        / 1e6
        * 5.0
    )
    output_cost = n * 900 / 1e6 * 25.0
    return {
        "requests": n,
        "system_tokens": system_tokens,
        "input_usd": input_cost * 0.5,
        "output_usd_est": output_cost * 0.5,
        "total_usd_est": (input_cost + output_cost) * 0.5,
    }


def verify_ready(source_city: str, target_city: str) -> list[str]:
    """Pre-flight checks. Returns human-readable problems, empty if good."""
    problems = []
    for city in (source_city, target_city):
        try:
            seeds.load_seeds(city)
        except seeds.SeedsError as exc:
            problems.append(str(exc))
    try:
        taxonomy.load_roles()
    except taxonomy.TaxonomyError as exc:
        problems.append(str(exc))
    if places.is_built():
        try:
            places.check_fresh()
        except places.StaleTableError as exc:
            problems.append(str(exc))
    return problems
