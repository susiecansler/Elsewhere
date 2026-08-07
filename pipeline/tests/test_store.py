from __future__ import annotations

import pytest

from elsewhere import store


@pytest.fixture
def db(tmp_path):
    return tmp_path / "judgments.db"


def rec(db, reviewer, place, answer, **kw):
    store.record(reviewer, place, "austin", "chicago", answer, path=db, **kw)


# ─── Basics ───────────────────────────────────────────────────────────────


def test_records_and_reads_back(db):
    rec(db, "Sam", "H-E-B", "Mariano's")
    assert store.for_reviewer("Sam", "chicago", db) == {"H-E-B": "Mariano's"}


def test_rejudging_replaces_your_own_answer(db):
    rec(db, "Sam", "H-E-B", "Jewel-Osco")
    rec(db, "Sam", "H-E-B", "Mariano's")
    assert store.for_reviewer("Sam", "chicago", db) == {"H-E-B": "Mariano's"}
    assert len(store.all_judgments(db)) == 1


def test_reviewer_names_are_normalized(db):
    # Case and spacing must not fork one person into two reviewers.
    rec(db, "  Sam   Cohen ", "H-E-B", "Mariano's")
    assert store.for_reviewer("Sam Cohen", "chicago", db) == {"H-E-B": "Mariano's"}


def test_blank_reviewer_is_rejected(db):
    with pytest.raises(ValueError, match="reviewer name is required"):
        rec(db, "   ", "H-E-B", "Mariano's")


def test_forget_removes_only_your_own(db):
    rec(db, "Sam", "H-E-B", "Mariano's")
    rec(db, "Alex", "H-E-B", "Pete's Fresh Market")
    store.forget("Sam", "H-E-B", "chicago", db)

    assert store.for_reviewer("Sam", "chicago", db) == {}
    assert store.for_reviewer("Alex", "chicago", db) == {"H-E-B": "Pete's Fresh Market"}


# ─── Multiple reviewers ───────────────────────────────────────────────────


def test_reviewers_do_not_clobber_each_other(db):
    """The whole reason this isn't a JSONL rewrite.

    Two people judging the same place concurrently must both survive; the
    read-modify-write it replaces would lose whichever wrote first.
    """
    rec(db, "Sam", "H-E-B", "Mariano's")
    rec(db, "Alex", "H-E-B", "Pete's Fresh Market")
    rec(db, "Jo", "H-E-B", "Mariano's")

    assert len(store.all_judgments(db)) == 3
    assert store.count(db) == 1  # one distinct place
    assert store.reviewers(db) == {"Sam": 1, "Alex": 1, "Jo": 1}


def test_consensus_keeps_every_distinct_answer(db):
    """Disagreement is signal, not a conflict to resolve.

    Two locals naming different places means the role is contested;
    collapsing to a majority would score a correct match as wrong.
    """
    rec(db, "Sam", "H-E-B", "Mariano's")
    rec(db, "Alex", "H-E-B", "Pete's Fresh Market")

    entry = store.consensus("chicago", path=db)["H-E-B"]
    assert set(entry["accepted"]) == {"Mariano's", "Pete's Fresh Market"}
    assert entry["split"] is True
    assert sorted(entry["reviewers"]) == ["Alex", "Sam"]


def test_agreement_is_not_marked_as_a_split(db):
    rec(db, "Sam", "H-E-B", "Mariano's")
    rec(db, "Alex", "H-E-B", "Mariano's")

    entry = store.consensus("chicago", path=db)["H-E-B"]
    assert entry["accepted"] == ["Mariano's"]
    assert entry["split"] is False


def test_consensus_can_exclude_a_reviewer(db):
    # An open link means a stranger can submit. Attribution is what makes
    # that recoverable.
    rec(db, "Sam", "H-E-B", "Mariano's")
    rec(db, "spammer", "H-E-B", "asdfasdf")

    entry = store.consensus("chicago", exclude={"spammer"}, path=db)["H-E-B"]
    assert entry["accepted"] == ["Mariano's"]
    assert entry["split"] is False


def test_consensus_is_scoped_to_the_target_city(db):
    store.record("Sam", "H-E-B", "austin", "chicago", "Mariano's", path=db)
    store.record("Sam", "H-E-B", "austin", "denver", "King Soopers", path=db)

    assert store.consensus("chicago", path=db)["H-E-B"]["accepted"] == ["Mariano's"]
    assert store.consensus("denver", path=db)["H-E-B"]["accepted"] == ["King Soopers"]


def test_custom_answers_are_flagged(db):
    rec(db, "Sam", "H-E-B", "Some Local Place", custom=True)
    assert store.all_judgments(db)[0].custom is True


def test_empty_store_is_empty(db):
    assert store.all_judgments(db) == []
    assert store.reviewers(db) == {}
    assert store.count(db) == 0
    assert store.consensus("chicago", path=db) == {}
