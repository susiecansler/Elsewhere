"""Scoring generated matches against ground truth.

This is the phase that decides whether role-based matching actually beats
category matching. Everything before it is opinion.

⚠️  On the provenance of ground truth. Entries carry a `provenance` field:

    mined       — an actual local said this, in a subreddit thread. Real
                  evidence, independent of the model being scored.
    provisional — written from the same model-family knowledge that generates
                  the matches. Useful as a smoke test, and NOT valid evidence:
                  scoring Opus 5's answers against answers Opus 5 wrote is
                  circular, and will flatter the model.

`score()` reports the two separately and refuses to report a headline number
from provisional data alone. The MVP success criteria (top-1 >= 60%, top-3
>= 85%) are only meaningful against mined entries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from elsewhere.models import Match
from elsewhere.places import normalize
from elsewhere.taxonomy import REPO_ROOT

EVAL_DIR = REPO_ROOT / "data" / "eval"
GROUND_TRUTH_PATH = EVAL_DIR / "ground_truth.jsonl"

Provenance = Literal["mined", "reviewed", "provisional"]

#: Provenances that count as evidence. Both are independent of the model being
#: scored, which is the property that matters — `mined` comes from strangers
#: on the internet, `reviewed` from a human who knows the city. Neither was
#: written by the thing under test.
INDEPENDENT: tuple[str, ...] = ("mined", "reviewed")


class GroundTruth(BaseModel):
    """One human-sourced answer.

    `accepted` holds multiple answers deliberately. When locals disagree about
    the Chicago H-E-B, that disagreement says the role is genuinely contested;
    collapsing it to one answer would score a correct match as wrong.
    """

    source_name: str
    source_city: str
    target_city: str
    accepted: list[str] = Field(min_length=1)
    provenance: Provenance
    source_url: str | None = None
    note: str | None = None


def load_ground_truth(path: Path | None = None) -> list[GroundTruth]:
    path = path or GROUND_TRUTH_PATH
    if not path.exists():
        return []
    with path.open() as f:
        return [GroundTruth.model_validate_json(line) for line in f if line.strip()]


def write_ground_truth(entries: list[GroundTruth], path: Path | None = None) -> None:
    path = path or GROUND_TRUTH_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for e in entries:
            f.write(e.model_dump_json() + "\n")


# ─── Scoring ──────────────────────────────────────────────────────────────


def _matches_any(candidate: str, accepted: list[str]) -> bool:
    """Compare on normalized names.

    Reuses the places normalizer so "Mariano's", "Marianos", and "Mariano's
    Fresh Market" all count as the same answer. Substring both ways because
    locals write the short form and the model often writes the full one.
    """
    c = normalize(candidate)
    if not c:
        return False
    for a in accepted:
        n = normalize(a)
        if not n:
            continue
        if c == n or c.startswith(n + " ") or n.startswith(c + " "):
            return True
    return False


#: (source name, what was ranked first, what was accepted)
Miss = tuple[str, str, list[str]]


@dataclass
class Score:
    total: int = 0
    top1: int = 0
    top3: int = 0
    missing: list[str] = field(default_factory=list)
    #: Right answer nowhere in the top 3 — the role model is wrong.
    wrong: list[Miss] = field(default_factory=list)
    #: Right answer present but not ranked first — the role is right and only
    #: the ordering is off. Kept separate because it's a different fix, and
    #: it's the most actionable failure mode.
    misranked: list[Miss] = field(default_factory=list)

    @property
    def top1_rate(self) -> float:
        return self.top1 / self.total if self.total else 0.0

    @property
    def top3_rate(self) -> float:
        return self.top3 / self.total if self.total else 0.0

    @property
    def failures(self) -> list[Miss]:
        """Everything that didn't hit top-1, mis-ranked first."""
        return self.misranked + self.wrong


def score_subset(matches: list[Match], truth: list[GroundTruth]) -> Score:
    by_name = {normalize(m.source.name): m for m in matches}
    out = Score()

    for entry in truth:
        match = by_name.get(normalize(entry.source_name))
        if match is None:
            out.missing.append(entry.source_name)
            continue

        out.total += 1
        names = [c.name for c in match.candidates]
        first = names[0] if names else "—"
        if names and _matches_any(names[0], entry.accepted):
            out.top1 += 1
            out.top3 += 1
        elif any(_matches_any(n, entry.accepted) for n in names[:3]):
            out.top3 += 1
            out.misranked.append((entry.source_name, first, entry.accepted))
        else:
            out.wrong.append((entry.source_name, first, entry.accepted))

    return out


def score(matches: list[Match], truth: list[GroundTruth]) -> dict[str, Score]:
    """Score, partitioned by provenance.

    Returns a dict keyed by provenance. Callers should report `mined` as the
    headline and treat `provisional` as a smoke test only.
    """
    return {
        prov: score_subset(matches, [t for t in truth if t.provenance == prov])
        for prov in ("mined", "reviewed", "provisional")
        if any(t.provenance == prov for t in truth)
    }


#: Below this, the confidence interval is wide enough that a taxonomy
#: revision's effect on the score is unreadable.
MIN_INDEPENDENT = 30


def independent_total(scores: dict[str, Score]) -> int:
    return sum(s.total for prov, s in scores.items() if prov in INDEPENDENT)


def is_reportable(scores: dict[str, Score]) -> bool:
    """Whether there is enough model-independent evidence for a headline."""
    return independent_total(scores) >= MIN_INDEPENDENT


# ─── Reddit mining ────────────────────────────────────────────────────────

#: Phrasings locals actually use when asking this question unprompted. These
#: are the highest-signal source of real ground truth.
MINING_QUERIES = [
    "equivalent of",
    "version of",
    "our answer to",
    "closest thing to",
    "like {source} but in",
    "moving from {source_city}",
]

SUBREDDITS = {
    "austin": ["austin", "AustinFood"],
    "chicago": ["chicago", "AskChicago", "chicagofood"],
}


def mining_plan(source_city: str, target_city: str) -> list[dict[str, str]]:
    """The search matrix a miner should execute.

    Split out from execution so the plan is inspectable (and testable)
    without credentials.
    """
    plan = []
    for sub in SUBREDDITS.get(target_city, []):
        for phrase in ["equivalent of", "version of", "answer to", "closest thing to"]:
            plan.append(
                {
                    "subreddit": sub,
                    "query": f"{source_city} {phrase}",
                    "source_city": source_city,
                    "target_city": target_city,
                }
            )
    return plan


def stub_mined_file(source_city: str, target_city: str) -> Path:
    """Where a mining run should drop raw hits for hand-cleaning."""
    return EVAL_DIR / f"raw-{source_city}-{target_city}.jsonl"


def load_raw_hits(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]
