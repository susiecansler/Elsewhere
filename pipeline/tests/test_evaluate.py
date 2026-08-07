from __future__ import annotations

import pytest

from elsewhere import evaluate
from elsewhere.models import Candidate, Match, Place, PriceTier, Reach


def make_match(source_name: str, candidates: list[str]) -> Match:
    return Match(
        source=Place(name=source_name, city="austin", category="grocery"),
        target_city="chicago",
        role_tags=["regional_grocery_cult"],
        price_tier=PriceTier.MODERATE,
        reach=Reach.REGIONAL,
        candidates=[Candidate(name=n, reasoning="because", confidence=0.8) for n in candidates],
    )


def truth(source_name: str, accepted: list[str], provenance="mined") -> evaluate.GroundTruth:
    return evaluate.GroundTruth(
        source_name=source_name,
        source_city="austin",
        target_city="chicago",
        accepted=accepted,
        provenance=provenance,
    )


# ─── Name comparison ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "candidate,accepted",
    [
        ("Mariano's", ["Mariano's"]),
        ("Marianos", ["Mariano's"]),
        ("Mariano's Fresh Market", ["Mariano's"]),
        ("Mariano's", ["Mariano's Fresh Market"]),
        ("The Green Mill", ["Green Mill"]),
    ],
)
def test_name_variants_count_as_the_same_answer(candidate, accepted):
    assert evaluate._matches_any(candidate, accepted)


@pytest.mark.parametrize(
    "candidate,accepted",
    [
        ("Jewel-Osco", ["Mariano's"]),
        ("Aldi", ["Mariano's", "Pete's Fresh Market"]),
        ("", ["Mariano's"]),
    ],
)
def test_wrong_answers_do_not_count(candidate, accepted):
    assert not evaluate._matches_any(candidate, accepted)


def test_partial_word_does_not_match():
    # "Mari" must not match "Mariano's" — prefix comparison is word-boundary
    # aware, otherwise near-misses score as hits and inflate the number.
    assert not evaluate._matches_any("Mari", ["Mariano's"])


# ─── Scoring ──────────────────────────────────────────────────────────────


def test_top1_hit():
    s = evaluate.score_subset(
        [make_match("H-E-B", ["Mariano's", "Jewel-Osco", "Aldi"])],
        [truth("H-E-B", ["Mariano's"])],
    )
    assert (s.top1, s.top3, s.total) == (1, 1, 1)


def test_top3_but_not_top1():
    s = evaluate.score_subset(
        [make_match("H-E-B", ["Jewel-Osco", "Aldi", "Mariano's"])],
        [truth("H-E-B", ["Mariano's"])],
    )
    assert (s.top1, s.top3) == (0, 1)
    # Recorded as mis-ranked, not wrong: the role model found the right
    # answer and only the ordering failed, which is a different fix.
    assert s.misranked and s.misranked[0][1] == "Jewel-Osco"
    assert not s.wrong


def test_complete_miss():
    s = evaluate.score_subset(
        [make_match("H-E-B", ["Jewel-Osco", "Aldi", "Whole Foods"])],
        [truth("H-E-B", ["Mariano's"])],
    )
    assert (s.top1, s.top3, s.total) == (0, 0, 1)


def test_any_accepted_answer_counts():
    # Locals disagreeing is signal, not noise — a match to any accepted
    # answer is correct.
    s = evaluate.score_subset(
        [make_match("Zilker Park", ["Grant Park"])],
        [truth("Zilker Park", ["Millennium Park", "Grant Park", "Lincoln Park"])],
    )
    assert s.top1 == 1


def test_ungenerated_entries_are_tracked_not_counted_wrong():
    # An entry with no generated match must not be scored as a failure —
    # that would conflate coverage gaps with quality gaps.
    s = evaluate.score_subset([], [truth("H-E-B", ["Mariano's"])])
    assert s.total == 0
    assert s.missing == ["H-E-B"]


def test_rates_on_empty_score_do_not_divide_by_zero():
    s = evaluate.Score()
    assert s.top1_rate == 0.0 and s.top3_rate == 0.0


# ─── Provenance ───────────────────────────────────────────────────────────


def test_score_partitions_by_provenance():
    matches = [make_match("A", ["X"]), make_match("B", ["Y"])]
    entries = [truth("A", ["X"], "mined"), truth("B", ["Y"], "provisional")]
    scores = evaluate.score(matches, entries)
    assert set(scores) == {"mined", "provisional"}
    assert scores["mined"].total == 1
    assert scores["provisional"].total == 1


def test_reviewed_counts_as_independent_evidence():
    """A human who knows the city wasn't written by the model under test.

    That independence — not the collection method — is what makes a pair
    valid evidence, so reviewed and mined count the same.
    """
    matches = [make_match(f"P{i}", ["X"]) for i in range(30)]
    entries = [truth(f"P{i}", ["X"], "reviewed") for i in range(30)]
    assert evaluate.is_reportable(evaluate.score(matches, entries))


def test_mined_and_reviewed_accumulate_together():
    matches = [make_match(f"P{i}", ["X"]) for i in range(30)]
    entries = [truth(f"P{i}", ["X"], "mined" if i % 2 else "reviewed") for i in range(30)]
    scores = evaluate.score(matches, entries)
    assert evaluate.independent_total(scores) == 30
    assert evaluate.is_reportable(scores)


def test_provisional_never_counts_toward_the_threshold():
    matches = [make_match(f"P{i}", ["X"]) for i in range(60)]
    entries = [truth(f"P{i}", ["X"], "reviewed") for i in range(10)] + [
        truth(f"P{i}", ["X"], "provisional") for i in range(10, 60)
    ]
    scores = evaluate.score(matches, entries)
    assert evaluate.independent_total(scores) == 10
    assert not evaluate.is_reportable(scores)


def test_provisional_alone_is_never_reportable():
    """Scoring Opus 5's matches against ground truth Opus 5 wrote is circular.

    The guard exists so a flattering provisional number can't be mistaken for
    evidence that the matching model works.
    """
    matches = [make_match(f"P{i}", ["X"]) for i in range(50)]
    entries = [truth(f"P{i}", ["X"], "provisional") for i in range(50)]
    assert not evaluate.is_reportable(evaluate.score(matches, entries))


def test_enough_mined_pairs_is_reportable():
    matches = [make_match(f"P{i}", ["X"]) for i in range(30)]
    entries = [truth(f"P{i}", ["X"], "mined") for i in range(30)]
    assert evaluate.is_reportable(evaluate.score(matches, entries))


def test_too_few_mined_pairs_is_not_reportable():
    matches = [make_match(f"P{i}", ["X"]) for i in range(10)]
    entries = [truth(f"P{i}", ["X"], "mined") for i in range(10)]
    assert not evaluate.is_reportable(evaluate.score(matches, entries))


# ─── The shipped ground-truth file ────────────────────────────────────────


def test_shipped_ground_truth_loads():
    entries = evaluate.load_ground_truth()
    assert len(entries) >= 40


def test_shipped_ground_truth_is_honestly_labelled():
    """Nothing model-authored may masquerade as independent evidence.

    Asserts the labelling is valid, not that everything is still
    provisional — reviewed entries appearing here is the goal, and must not
    break the suite when it happens.
    """
    entries = evaluate.load_ground_truth()
    assert entries
    valid = {"mined", "reviewed", "provisional"}
    assert all(e.provenance in valid for e in entries)
    # The 46 seeded pairs were written by the model family under test and
    # must stay labelled as such.
    seeded = [e for e in entries if e.note and "canonical case" in e.note]
    assert all(e.provenance == "provisional" for e in seeded)


def test_shipped_ground_truth_sources_exist_in_the_seed_corpus():
    from elsewhere import seeds

    names = {p.name for p in seeds.load_seeds("austin")}
    unknown = [e.source_name for e in evaluate.load_ground_truth() if e.source_name not in names]
    assert not unknown, f"ground truth references non-seed places: {unknown}"


def test_mining_plan_is_non_empty():
    plan = evaluate.mining_plan("austin", "chicago")
    assert plan
    assert all("subreddit" in item and "query" in item for item in plan)
