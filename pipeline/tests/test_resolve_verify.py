from __future__ import annotations

import pytest

from elsewhere import places, resolve, verify
from elsewhere.models import Candidate, Match, Place, PriceTier, Reach

needs_table = pytest.mark.skipif(not places.is_built(), reason="requires `elsewhere places build`")


# ─── Entity resolution ────────────────────────────────────────────────────


@pytest.mark.parametrize("query", ["HEB", "H-E-B", "heb", "h e b", "HEB grocery"])
def test_heb_variants_all_resolve_to_one_seed(query):
    """The plan's stated definition of done for phase 5."""
    hit = resolve.resolve(query, "austin")
    assert hit is not None, query
    assert hit.place.name == "H-E-B"


@pytest.mark.parametrize(
    "query,expected",
    [
        ("torchys", "Torchy's Tacos"),
        ("Torchy's", "Torchy's Tacos"),
        ("franklin bbq", "Franklin Barbecue"),
        ("the greenbelt", "Barton Creek Greenbelt"),
        ("barton springs", "Barton Springs Pool"),
        ("alamo drafthouse", "Alamo Drafthouse Cinema"),
        ("ACL Live", "Austin City Limits Live"),
    ],
)
def test_common_shorthand_resolves(query, expected):
    hit = resolve.resolve(query, "austin")
    assert hit is not None, query
    assert hit.place.name == expected


def test_real_name_beats_another_places_alias():
    # "Buffalo Exchange" is a seed in its own right; it must not be shadowed.
    hit = resolve.resolve("Buffalo Exchange", "austin")
    assert hit is not None and hit.place.name == "Buffalo Exchange"


@pytest.mark.parametrize("query", ["", "   ", "!!!"])
def test_empty_queries_return_none(query):
    assert resolve.resolve(query, "austin") is None


def test_vague_query_misses_rather_than_guessing():
    # A bare category should not confidently resolve to one business.
    hit = resolve.resolve("coffee", "austin")
    assert hit is None or hit.how != "fuzzy"


def test_nonsense_does_not_resolve():
    assert resolve.resolve("zzzzqqq nonexistent", "austin") is None


def test_suggest_offers_alternatives():
    near = resolve.suggest("torchy", "austin")
    assert any(p.name == "Torchy's Tacos" for p in near)


def test_suggest_is_deduplicated():
    near = resolve.suggest("barton", "austin")
    assert len({p.name for p in near}) == len(near)


# ─── Verification ─────────────────────────────────────────────────────────


def make_match(source: str, candidates: list[str]) -> Match:
    return Match(
        source=Place(name=source, city="austin", category="grocery"),
        target_city="chicago",
        role_tags=["regional_grocery_cult"],
        price_tier=PriceTier.MODERATE,
        reach=Reach.REGIONAL,
        candidates=[Candidate(name=n, reasoning="r", confidence=0.8) for n in candidates],
    )


@needs_table
def test_real_places_verify():
    matches, rejects, report = verify.verify_matches(
        [make_match("H-E-B", ["Mariano's", "Jewel-Osco"])]
    )
    assert report.verified == 2
    assert not rejects
    assert all(c.verified for c in matches[0].candidates)


@needs_table
def test_invented_places_are_flagged():
    matches, rejects, report = verify.verify_matches(
        [make_match("H-E-B", ["Zzyzx Imaginary Grocery Emporium"])]
    )
    assert report.unverified == 1
    assert rejects and rejects[0]["reason"] == "not_found"
    assert matches[0].candidates[0].verified is False


@needs_table
def test_nothing_is_dropped():
    """Flags, never filters.

    An absence in Overture is not proof of a hallucination — its coverage of
    small local businesses is uneven, so deleting on a miss would discard good
    matches and inflate apparent precision.
    """
    original = make_match("H-E-B", ["Mariano's", "Zzyzx Imaginary Emporium", "Aldi"])
    matches, _, _ = verify.verify_matches([original])
    assert len(matches) == 1
    assert len(matches[0].candidates) == 3


@needs_table
def test_rejects_carry_enough_context_to_act_on():
    _, rejects, _ = verify.verify_matches([make_match("H-E-B", ["Zzyzx Imaginary Emporium"])])
    r = rejects[0]
    assert {"source", "candidate", "target_city", "rank", "reason"} <= set(r)


@needs_table
def test_top_candidate_health_is_tracked_separately():
    # An unverified third choice barely matters; an unverified top pick is the
    # answer the product would actually show.
    matches, _, _ = verify.verify_matches(
        [
            make_match("A", ["Mariano's", "Zzyzx Imaginary"]),
            make_match("B", ["Zzyzx Also Imaginary", "Mariano's"]),
        ]
    )
    ok, total = verify.top_candidate_health(matches)
    assert (ok, total) == (1, 2)


def test_verify_without_places_table_raises(monkeypatch):
    monkeypatch.setattr(places, "is_built", lambda: False)
    with pytest.raises(RuntimeError, match="no places table"):
        verify.verify_matches([make_match("H-E-B", ["Mariano's"])])
