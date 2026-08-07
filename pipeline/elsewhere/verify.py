"""Verify generated candidates against the places substrate.

Catches two things: places the model invented, and places that closed. Both
would otherwise reach the eval and be scored as if real.

**Flags, never filters.** Overture's coverage of small local businesses is
uneven, so an unverified candidate is not evidence of a hallucination — it is
an absence of evidence. Auto-deleting on a lookup miss would silently discard
good matches and quietly inflate apparent precision. Every rejection is
written out with a reason, and that file is meant to be read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from elsewhere import places
from elsewhere.models import Match
from elsewhere.taxonomy import REPO_ROOT

MATCHES_DIR = REPO_ROOT / "data" / "matches"

#: Role-to-category checks are deliberately absent. Overture's taxonomy is
#: much finer than the role vocabulary and mapping between them would produce
#: more false rejections than real catches. Existence and liveness only.


@dataclass
class VerificationReport:
    total_candidates: int = 0
    verified: int = 0
    unverified: int = 0

    @property
    def unverified_rate(self) -> float:
        return self.unverified / self.total_candidates if self.total_candidates else 0.0

    def summary(self) -> str:
        return (
            f"{self.verified}/{self.total_candidates} candidates verified "
            f"({self.unverified_rate:.1%} unverified)"
        )


def verify_matches(matches: list[Match]) -> tuple[list[Match], list[dict], VerificationReport]:
    """Annotate every candidate with verification state.

    Returns (matches, rejects, report). Matches are returned intact — nothing
    is dropped. `rejects` is the inspection list.
    """
    report = VerificationReport()
    rejects: list[dict] = []

    if not places.is_built():
        raise RuntimeError("no places table — run `elsewhere places build` first")
    places.check_fresh()

    for match in matches:
        for candidate in match.candidates:
            report.total_candidates += 1
            hit = places.lookup(candidate.name, match.target_city)

            if hit is not None:
                candidate.verified = True
                candidate.fsq_place_id = hit.fsq_place_id
                candidate.verification_note = f"matched {hit.name!r} ({hit.category})"
                report.verified += 1
            else:
                candidate.verified = False
                candidate.verification_note = "not found in Overture for this city"
                report.unverified += 1
                rejects.append(
                    {
                        "source": match.source.name,
                        "candidate": candidate.name,
                        "target_city": match.target_city,
                        "confidence": candidate.confidence,
                        "rank": match.candidates.index(candidate) + 1,
                        "reason": "not_found",
                    }
                )

    return matches, rejects, report


def verified_path(source_city: str, target_city: str) -> Path:
    return MATCHES_DIR / f"{source_city}-{target_city}.verified.jsonl"


def rejects_path(source_city: str, target_city: str) -> Path:
    return MATCHES_DIR / f"{source_city}-{target_city}.rejects.jsonl"


def write_rejects(rejects: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rejects:
            f.write(json.dumps(r) + "\n")


def top_candidate_health(matches: list[Match]) -> tuple[int, int]:
    """How many *first-ranked* candidates verified.

    Worth separating from the overall rate: an unverified third choice barely
    matters, an unverified top pick is the answer the product would show.
    """
    tops = [m.top() for m in matches]
    present = [c for c in tops if c is not None]
    return sum(1 for c in present if c.verified), len(present)
