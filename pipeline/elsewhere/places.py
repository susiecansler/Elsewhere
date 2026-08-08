"""Structured place data, used to verify that generated candidates are real.

Source is **Overture Maps** (CDLA-Permissive 2.0), not Foursquare OS Places as
originally planned: Foursquare retired its public S3 parquet path in favour of
the token-gated Places Portal, and its Hugging Face mirror is gated too.
Overture is genuinely unauthenticated, carries the Foursquare-derived places
under Apache 2.0, and is refreshed monthly. See docs/data-strategy notes.

Everything here is *verification* substrate. It cannot tell you that Mariano's
fills H-E-B's role — only that Mariano's exists, is a grocery, and is in
Chicago. That is exactly the job it should have.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import duckdb

from elsewhere.taxonomy import REPO_ROOT

PLACES_DIR = REPO_ROOT / "data" / "places"
DB_PATH = PLACES_DIR / "places.duckdb"

# Pin the release so a rebuild is reproducible. Bump deliberately; Overture
# ships monthly and an unpinned "latest" would silently change the corpus
# under an eval run.
OVERTURE_RELEASE = "2026-07-22.0"
OVERTURE_SRC = f"s3://overturemaps-us-west-2/release/{OVERTURE_RELEASE}/theme=places/type=place/*"


@dataclass(frozen=True)
class BBox:
    """Metro bounding box. Generous — better to over-include than clip suburbs."""

    west: float
    south: float
    east: float
    north: float


CITY_BBOXES: dict[str, BBox] = {
    "austin": BBox(west=-98.05, south=30.05, east=-97.50, north=30.60),
    "chicago": BBox(west=-88.10, south=41.55, east=-87.45, north=42.15),
    # Wide enough to take in Beaverton and Lake Oswego — a few Portland
    # institutions people name are across the county line.
    "portland": BBox(west=-122.90, south=45.35, east=-122.40, north=45.65),
}


def _connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    PLACES_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH), read_only=read_only)
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    return con


def normalize_fingerprint() -> str:
    """Fingerprint of `normalize`'s behaviour, stored alongside the built table.

    `norm_name` is materialized at build time but `lookup` normalizes at query
    time. Change `normalize` without rebuilding and the two silently stop
    agreeing — which shows up as a mysterious drop in match rate, not an
    error. Probes rather than hashing source so comments don't invalidate it.
    """
    probes = [
        "The Tarrytown Cafe, Inc.",
        "H-E-B #42",
        "Lou Malnati's Pizzeria",
        "St. Elmo Brewing Co",
    ]
    return hashlib.sha256("|".join(normalize(p) for p in probes).encode()).hexdigest()[:16]


def normalize(name: str) -> str:
    """Fold a name to a comparable key.

    Strips punctuation and common suffixes so "Torchy's Tacos", "Torchys", and
    "TORCHY'S TACOS #4" collapse together. Deliberately aggressive — this is
    for candidate *lookup*, where a false pair is cheaper than a missed one.
    """
    n = name.lower().strip()
    n = re.sub(r"[''`]", "", n)
    n = re.sub(r"#\s*\d+\b", " ", n)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    # Only corporate boilerplate and articles. Business nouns (cafe, bar,
    # restaurant, store) stay: stripping them collapses "Tarrytown Cafe" into
    # the neighborhood "Tarrytown" and "South Congress Cafe" into the street,
    # which produced confident joins between a district and a random diner.
    n = re.sub(r"\b(the|a|an|inc|llc|co|company|corp|ltd)\b", " ", n)
    n = re.sub(r"\s+", " ", n)
    return n.strip()


def build(cities: list[str], release: str | None = None) -> dict[str, int]:
    """Pull bbox-filtered places for each city into the local DuckDB.

    Queries Overture's parquet over HTTPS with predicate pushdown on `bbox`,
    so this reads a few hundred MB rather than the full global dataset.
    """
    src = (
        OVERTURE_SRC
        if release is None
        else (f"s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*")
    )
    unknown = [c for c in cities if c not in CITY_BBOXES]
    if unknown:
        raise ValueError(f"no bounding box defined for {unknown}; add one to CITY_BBOXES")

    con = _connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS places (
            fsq_place_id  VARCHAR,
            city          VARCHAR,
            name          VARCHAR,
            norm_name     VARCHAR,
            category      VARCHAR,
            alt_categories VARCHAR[],
            confidence    DOUBLE,
            lon           DOUBLE,
            lat           DOUBLE,
            website       VARCHAR
        )
    """)
    con.create_function("norm", normalize, ["VARCHAR"], "VARCHAR")

    counts: dict[str, int] = {}
    for city in cities:
        box = CITY_BBOXES[city]
        con.execute("DELETE FROM places WHERE city = ?", [city])
        con.execute(
            f"""
            INSERT INTO places
            SELECT
                id,
                ? AS city,
                names.primary,
                norm(names.primary),
                categories.primary,
                categories.alternate,
                confidence,
                bbox.xmin,
                bbox.ymin,
                websites[1]
            FROM read_parquet('{src}', hive_partitioning=1)
            WHERE bbox.xmin BETWEEN ? AND ?
              AND bbox.ymin BETWEEN ? AND ?
              AND names.primary IS NOT NULL
            """,
            [city, box.west, box.east, box.south, box.north],
        )
        counts[city] = con.execute("SELECT count(*) FROM places WHERE city = ?", [city]).fetchone()[
            0
        ]

    con.execute("CREATE INDEX IF NOT EXISTS idx_norm ON places(city, norm_name)")
    con.execute("CREATE TABLE IF NOT EXISTS build_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    con.execute("DELETE FROM build_meta")
    con.executemany(
        "INSERT INTO build_meta VALUES (?, ?)",
        [
            ("normalize_fingerprint", normalize_fingerprint()),
            ("overture_release", release or OVERTURE_RELEASE),
        ],
    )
    con.close()
    return counts


class StaleTableError(Exception):
    """Raised when the built table no longer agrees with `normalize`."""


def check_fresh() -> None:
    """Fail loudly if `normalize` changed since the table was built."""
    if not is_built():
        return
    con = _connect(read_only=True)
    try:
        row = con.execute(
            "SELECT value FROM build_meta WHERE key = 'normalize_fingerprint'"
        ).fetchone()
    except duckdb.CatalogException:
        row = None
    finally:
        con.close()

    current = normalize_fingerprint()
    if row is None or row[0] != current:
        raise StaleTableError(
            "the places table was built with a different `normalize` "
            "implementation, so stored keys no longer match query-time keys. "
            "Rebuild with `elsewhere places build`."
        )


def is_built() -> bool:
    return DB_PATH.exists()


def city_counts() -> dict[str, int]:
    """Rows per city in the local table."""
    if not is_built():
        return {}
    con = _connect(read_only=True)
    rows = con.execute("SELECT city, count(*) FROM places GROUP BY city ORDER BY city").fetchall()
    con.close()
    return dict(rows)


@dataclass
class PlaceHit:
    fsq_place_id: str
    name: str
    category: str | None
    confidence: float | None
    lon: float
    lat: float


#: Seed categories naming *areas* rather than businesses.
AREA_CATEGORIES = ("neighborhood_",)

#: Overture categories an area seed may legitimately resolve to. Some
#: neighborhoods really are in the POI theme ("Wicker Park" the park, "The
#: 606" the trail); most are not, and without this an area name latches onto
#: whatever business shares it — "East Austin" matched a post office.
AREA_COMPATIBLE = (
    "park",
    "landmark",
    "neighborhood",
    "tourist",
    "monument",
    "plaza",
    "trail",
    "garden",
    "historical",
)


def _area_compatible(category: str | None) -> bool:
    if category is None:
        return True  # Overture leaves areas uncategorized more often than not
    return any(token in category for token in AREA_COMPATIBLE)


def lookup(
    name: str,
    city: str,
    aliases: list[str] | None = None,
    category: str | None = None,
) -> PlaceHit | None:
    """Find a place by name in a city, trying aliases before giving up.

    Exact normalized match first, then a *narrowing* prefix match: the stored
    name may be shorter than the query ("Barton Springs Pool" → "Barton
    Springs"), never longer. The reverse direction is what produced joins like
    "Tarrytown" → "Tarrytown Cafe" and "Lake Forest" → "Lake Forest College" —
    it appends tokens that change what the place *is*.

    No fuzzy edit-distance pass. A wrong join is worse than no join here,
    because phase 3 would read it as evidence that a hallucinated place is
    real.
    """
    if not is_built():
        return None

    keys = [k for k in (normalize(c) for c in [name, *(aliases or [])]) if k]
    if not keys:
        return None

    is_area = bool(category and category.startswith(AREA_CATEGORIES))

    con = _connect(read_only=True)
    try:
        for key in keys:
            row = con.execute(
                """
                SELECT fsq_place_id, name, category, confidence, lon, lat
                FROM places
                WHERE city = ? AND norm_name = ?
                ORDER BY confidence DESC NULLS LAST
                LIMIT 1
                """,
                [city, key],
            ).fetchone()
            if row:
                hit = PlaceHit(*row)
                if is_area and not _area_compatible(hit.category):
                    continue
                return hit

        if is_area:
            return None

        for key in keys:
            row = con.execute(
                """
                SELECT fsq_place_id, name, category, confidence, lon, lat
                FROM places
                WHERE city = ?
                  AND length(norm_name) >= 6
                  AND ? LIKE norm_name || ' %'
                ORDER BY length(norm_name) DESC, confidence DESC NULLS LAST
                LIMIT 1
                """,
                [city, key],
            ).fetchone()
            if row:
                return PlaceHit(*row)
    finally:
        con.close()
    return None
