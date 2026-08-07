from __future__ import annotations

import pytest

from elsewhere import places, seeds

pytestmark = pytest.mark.filterwarnings("ignore")

needs_table = pytest.mark.skipif(not places.is_built(), reason="requires `elsewhere places build`")


# ─── Normalization ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("H-E-B", "h e b"),
        ("H-E-B #42", "h e b"),
        ("Torchy's Tacos", "torchys tacos"),
        ("The Continental Club", "continental club"),
        ("St. Elmo Brewing Co", "st elmo brewing"),
        ("  Mariano's  ", "marianos"),
    ],
)
def test_normalize(raw, expected):
    assert places.normalize(raw) == expected


def test_normalize_keeps_business_nouns():
    # Stripping these collapsed "Tarrytown Cafe" into the neighborhood
    # "Tarrytown" and joined a district to a diner. They are load-bearing.
    assert places.normalize("Tarrytown Cafe") != places.normalize("Tarrytown")
    assert places.normalize("South Congress Cafe") != places.normalize("South Congress")
    assert places.normalize("Hyde Park Bar & Grill") != places.normalize("Hyde Park")


def test_normalize_fingerprint_is_stable():
    assert places.normalize_fingerprint() == places.normalize_fingerprint()
    assert len(places.normalize_fingerprint()) == 16


def test_area_compatibility():
    assert places._area_compatible(None)
    assert places._area_compatible("park")
    assert places._area_compatible("landmark_and_historical_building")
    assert not places._area_compatible("post_office")
    assert not places._area_compatible("american_restaurant")
    assert not places._area_compatible("college_university")


# ─── Joins against the real table ─────────────────────────────────────────


@needs_table
def test_table_is_fresh():
    places.check_fresh()


@needs_table
@pytest.mark.parametrize(
    "city,name",
    [
        ("austin", "H-E-B"),
        ("austin", "Franklin Barbecue"),
        ("austin", "Zilker Park"),
        ("chicago", "Mariano's"),
        ("chicago", "Lou Malnati's"),
        ("chicago", "Cloud Gate"),
    ],
)
def test_known_places_resolve(city, name):
    corpus = {p.name: p for p in seeds.load_seeds(city)}
    p = corpus[name]
    assert places.lookup(p.name, city, p.aliases, p.category) is not None


@needs_table
def test_narrowing_prefix_allowed():
    # Stored name may be shorter than the query: "Barton Springs Pool" should
    # still find "Barton Springs".
    hit = places.lookup("Barton Springs Pool", "austin", [], "outdoor_swim")
    assert hit is not None
    assert "barton springs" in places.normalize(hit.name)


@needs_table
@pytest.mark.parametrize(
    "city,name",
    [
        ("austin", "Tarrytown"),  # was matching "Tarrytown Cafe"
        ("austin", "South Congress"),  # was matching "South Congress Cafe"
        ("austin", "Hyde Park"),  # was matching "Hyde Park Bar & Grill"
        ("austin", "East Austin"),  # was matching a post office
        ("chicago", "Lake Forest"),  # was matching "Lake Forest College"
    ],
)
def test_area_names_do_not_latch_onto_businesses(city, name):
    """Regression: every one of these produced a confident false join.

    A wrong join is worse than none — phase 3 reads it as evidence that a
    hallucinated place is real.
    """
    corpus = {p.name: p for p in seeds.load_seeds(city)}
    p = corpus[name]
    hit = places.lookup(p.name, city, p.aliases, p.category)
    assert hit is None or places._area_compatible(hit.category), (
        f"{name} joined to {hit.name!r} ({hit.category})"
    )


@needs_table
def test_areas_that_really_are_pois_still_match():
    # The guard must not be so tight that genuine area POIs stop resolving.
    for city, name in [("chicago", "Wicker Park"), ("chicago", "The 606")]:
        corpus = {p.name: p for p in seeds.load_seeds(city)}
        p = corpus[name]
        assert places.lookup(p.name, city, p.aliases, p.category) is not None, name


@needs_table
def test_nonsense_does_not_resolve():
    assert places.lookup("Zzyzx Nonexistent Emporium", "austin", [], "grocery") is None


# ─── Seed corpora ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("city", ["austin", "chicago"])
def test_seed_file_loads(city):
    corpus = seeds.load_seeds(city)
    assert len(corpus) >= 100, "plan calls for ~120 institutions per side"
    assert all(p.city == city for p in corpus)


@pytest.mark.parametrize("city", ["austin", "chicago"])
def test_seed_categories_are_broad(city):
    # A role with no plausible candidate caps recall no matter how good the
    # taxonomy is, so guard the spread of the candidate pool.
    cats = {p.category for p in seeds.load_seeds(city)}
    assert len(cats) >= 25, f"{city} spans only {len(cats)} categories"


def test_duplicate_names_rejected(tmp_path):
    path = tmp_path / "dupe.yaml"
    path.write_text(
        "city: dupe\nplaces:\n"
        "  - {name: Twice, category: grocery}\n"
        "  - {name: Twice, category: bar_dive}\n"
    )
    with pytest.raises(seeds.SeedsError, match="duplicate place names"):
        seeds.load_seeds("dupe", path)


def test_city_mismatch_rejected(tmp_path):
    path = tmp_path / "x.yaml"
    path.write_text("city: austin\nplaces:\n  - {name: A Place, category: grocery}\n")
    with pytest.raises(seeds.SeedsError, match="declares city"):
        seeds.load_seeds("chicago", path)
