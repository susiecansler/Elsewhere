"""Outbound links for the places in the corpus.

The Overture table carries a website and coordinates for most places, but it
is a 156 MB DuckDB built from a few hundred MB of parquet — too big to track
and not present in the deployed container. So this extracts just the rows the
corpus actually names into a small JSON file that *is* tracked, and the web
app reads that.

Two consequences worth knowing:

* Only places that verification matched to an Overture row get a website.
  Everything else still gets a map link, which is derived from the name and
  city and so is always available.
* The file is a snapshot. A business that changes domains stays stale until
  someone re-runs `elsewhere links build`. That is the trade for not shipping
  the database, and it is the right one for links that are a convenience
  rather than the product.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from elsewhere import generate, places, verify
from elsewhere.taxonomy import REPO_ROOT

#: Tracked, unlike data/places/, which is gitignored as rebuildable bulk.
LINKS_PATH = REPO_ROOT / "data" / "links.json"

#: Cities are stored as slugs; a map query wants something a human would type.
CITY_QUERY = {
    "austin": "Austin, TX",
    "chicago": "Chicago, IL",
    "portland": "Portland, OR",
    "tokyo": "Tokyo, Japan",
    "mexico_city": "Mexico City, Mexico",
}


def map_url(name: str, city: str) -> str:
    """A Google Maps search for this place.

    Built from the name rather than a place id: Google's ids are Google's, and
    the terms only allow storing them under conditions this project doesn't
    meet. A search URL needs no key, no quota, and no agreement — and it lands
    on the listing, which is also where the reviews are.
    """
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(
        f"{name} {CITY_QUERY.get(city, city)}"
    )


def _clean(website: str | None) -> str | None:
    """Drop anything that isn't a plain http(s) link.

    The web app renders these as anchors, so a `javascript:` value from
    upstream data would be a script injection with extra steps.
    """
    if not website:
        return None
    site = website.strip()
    return site if site.startswith(("http://", "https://")) else None


#: Categories where the name denotes an area, not a business with a homepage.
#: A neighborhood shares its name with whatever opened there, so a name-based
#: join finds a plausible-looking row and attaches a wrong link: "The Domain"
#: matched a department store, "Mueller" a gym, "South Congress" a mall.
#: These places still get a map link, which is the correct one for them.
AREA_CATEGORIES = {"neighborhood", "park", "outdoor", "landmark"}


def corpus_places() -> dict[str, dict[str, str]]:
    """Every place the corpus names, by city, with the category it plays.

    Both sides count: a source place is the card's heading and a candidate is
    its answer, and both are worth linking. A candidate inherits its source's
    category — the match is role-for-role, so a neighborhood is answered with
    a neighborhood.
    """
    wanted: dict[str, dict[str, str]] = {}
    for src, tgt in _pairs():
        for m in _load(src, tgt):
            cat = m.source.category or ""
            wanted.setdefault(src, {})[m.source.name] = cat
            for c in m.candidates:
                wanted.setdefault(tgt, {}).setdefault(c.name, cat)
    return wanted


def _pairs() -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for suffix in (".raw.jsonl", ".verified.jsonl"):
        for path in generate.MATCHES_DIR.glob(f"*-*{suffix}"):
            stem = path.name.removesuffix(suffix)
            if "-" in stem:
                a, b = stem.split("-", 1)
                pairs.add((a, b))
    return sorted(pairs)


def _load(src: str, tgt: str) -> list:
    path = verify.verified_path(src, tgt)
    if not path.exists():
        path = generate.raw_path(src, tgt)
    return generate.read_matches(path)


def build() -> dict[str, int]:
    """Extract websites and coordinates for every place the corpus names.

    Matches by normalized name within the city, the same join verification
    uses, so a place linked here is a place verification would have accepted.
    """
    wanted = corpus_places()
    con = places._connect()

    out: dict[str, dict[str, Any]] = {}
    for city, names in sorted(wanted.items()):
        found: dict[str, Any] = {}
        for name, category in sorted(names.items()):
            is_area = category.split("_")[0] in AREA_CATEGORIES
            row = con.execute(
                """
                SELECT website, lon, lat FROM places
                WHERE city = ? AND norm_name = ?
                ORDER BY confidence DESC NULLS LAST
                LIMIT 1
                """,
                [city, places.normalize(name)],
            ).fetchone()
            if not row:
                continue
            website, lon, lat = row
            site = None if is_area else _clean(website)
            entry = {k: v for k, v in (("website", site),) if v}
            if lon is not None and lat is not None:
                entry["lon"], entry["lat"] = round(lon, 5), round(lat, 5)
            if entry:
                found[name] = entry
        out[city] = found

    LINKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LINKS_PATH.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    return {city: len(v) for city, v in out.items()}


def load() -> dict[str, dict[str, Any]]:
    """Read the snapshot, or nothing if it hasn't been built.

    Missing links degrade to map-only rather than failing: the site is useful
    without them, and a deploy shouldn't hinge on a derived file.
    """
    if not LINKS_PATH.exists():
        return {}
    try:
        return json.loads(LINKS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def for_place(index: dict[str, dict[str, Any]], name: str, city: str) -> dict[str, Any]:
    """What the card can show for one place.

    Always a map search link; a website where we trust one; and coordinates
    where the corpus knows them, which is what lets the card draw an actual
    map instead of just linking to one.
    """
    entry = index.get(city, {}).get(name, {})
    out: dict[str, Any] = {"map": map_url(name, city)}
    if entry.get("website"):
        out["website"] = entry["website"]
    if entry.get("lat") is not None and entry.get("lon") is not None:
        out["lat"], out["lon"] = entry["lat"], entry["lon"]
    return out


def path_for(_: object = None) -> Path:
    return LINKS_PATH
