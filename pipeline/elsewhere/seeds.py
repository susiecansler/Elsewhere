"""Seed corpus: load the hand-curated YAML, join to Foursquare, emit JSONL."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from elsewhere.models import Place
from elsewhere.taxonomy import REPO_ROOT

SEEDS_DIR = REPO_ROOT / "data" / "seeds"


class SeedsError(Exception):
    """Raised when a seed file is malformed."""


class SeedFile(BaseModel):
    """The on-disk shape of a curated seed list."""

    city: str
    places: list[Place] = Field(min_length=1)


def load_seeds(city: str, path: Path | None = None) -> list[Place]:
    """Load and validate a city's curated seed list.

    Stamps `city` onto each place from the file header so the curated YAML
    doesn't have to repeat it 120 times.
    """
    path = path or SEEDS_DIR / f"{city}.yaml"
    if not path.exists():
        raise SeedsError(f"no seed file at {path}")

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise SeedsError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise SeedsError(f"{path} must be a mapping with 'city' and 'places' keys")

    # Places in the file omit `city`; fill it from the header before validating.
    for entry in raw.get("places") or []:
        if isinstance(entry, dict):
            entry.setdefault("city", raw.get("city", city))

    try:
        parsed = SeedFile.model_validate(raw)
    except Exception as exc:
        raise SeedsError(f"invalid seed file {path}: {exc}") from exc

    if parsed.city != city:
        raise SeedsError(f"{path} declares city {parsed.city!r} but was loaded as {city!r}")

    names = [p.name for p in parsed.places]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise SeedsError(f"duplicate place names in {path}: {sorted(dupes)}")

    return parsed.places


def available_cities() -> list[str]:
    """Cities with a curated seed file."""
    return sorted(p.stem for p in SEEDS_DIR.glob("*.yaml"))


def write_jsonl(places: list[Place], path: Path) -> None:
    """Emit the built corpus, one place per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for place in places:
            f.write(place.model_dump_json() + "\n")


def read_jsonl(path: Path) -> list[Place]:
    """Read a built corpus."""
    if not path.exists():
        raise SeedsError(f"no built corpus at {path} — run `elsewhere seeds build` first")
    with path.open() as f:
        return [Place.model_validate(json.loads(line)) for line in f if line.strip()]


def coverage(places: list[Place]) -> tuple[int, int]:
    """Return (matched, total) against Foursquare.

    Reported rather than enforced. Foursquare's coverage of small local
    businesses is uneven, and an unmatched seed is still a perfectly good
    query — it just can't be verified automatically.
    """
    return sum(1 for p in places if p.fsq_place_id), len(places)
