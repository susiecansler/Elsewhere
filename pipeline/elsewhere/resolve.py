"""Turn what a user types into a place in the seed corpus.

"HEB", "H-E-B", "heb grocery", and "the heb on south lamar" all have to reach
the same seed. This runs on every query, so it stays local and free — no
model call on the serving path.

Deliberately not an LLM: it needs to be fast, it needs zero marginal cost, and
matching a string to one of 117 known names requires no world knowledge. The
one job the model is uniquely good at — knowing what H-E-B *means* — already
happened offline in generation.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from elsewhere import seeds
from elsewhere.models import Place
from elsewhere.places import normalize

#: Below this, a fuzzy hit is more likely noise than a typo. Tuned so
#: "torchys" reaches "Torchy's Tacos" but "coffee" doesn't reach "Cuvée
#: Coffee" — a vague query should miss rather than answer confidently.
FUZZY_CUTOFF = 0.72


@dataclass
class Resolution:
    place: Place
    how: str
    score: float = 1.0


def _index(corpus: list[Place]) -> dict[str, Place]:
    """Normalized key → place, covering names and aliases.

    Names win over aliases on collision: if one place's alias equals another's
    real name, the real name is the better answer.
    """
    index: dict[str, Place] = {}
    for place in corpus:
        for alias in place.aliases:
            key = normalize(alias)
            if key:
                index.setdefault(key, place)
    for place in corpus:
        key = normalize(place.name)
        if key:
            index[key] = place
    return index


def resolve(query: str, city: str, corpus: list[Place] | None = None) -> Resolution | None:
    """Best seed for a typed query, or None."""
    corpus = corpus if corpus is not None else seeds.load_seeds(city)
    key = normalize(query)
    if not key:
        return None

    index = _index(corpus)

    if key in index:
        return Resolution(index[key], how="exact")

    # Containment, longest key first so "heb grocery" prefers "h e b" over a
    # shorter incidental substring.
    contained = [k for k in index if k and (k in key or key in k)]
    if contained:
        best = max(contained, key=len)
        # Guard against a 2-char key matching everything.
        if len(best) >= 3:
            return Resolution(index[best], how="contains", score=len(best) / max(len(key), 1))

    close = difflib.get_close_matches(key, list(index), n=1, cutoff=FUZZY_CUTOFF)
    if close:
        score = difflib.SequenceMatcher(None, key, close[0]).ratio()
        return Resolution(index[close[0]], how="fuzzy", score=score)

    return None


def suggest(query: str, city: str, n: int = 5, corpus: list[Place] | None = None) -> list[Place]:
    """Nearest seeds for a failed query, so the CLI can say 'did you mean'."""
    corpus = corpus if corpus is not None else seeds.load_seeds(city)
    index = _index(corpus)
    key = normalize(query)
    if not key:
        return []
    keys = difflib.get_close_matches(key, list(index), n=n, cutoff=0.4)
    seen, out = set(), []
    for k in keys:
        place = index[k]
        if place.name not in seen:
            seen.add(place.name)
            out.append(place)
    return out
