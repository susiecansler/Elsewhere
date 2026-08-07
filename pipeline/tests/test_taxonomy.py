from __future__ import annotations

from pathlib import Path

import pytest

from elsewhere import taxonomy
from elsewhere.models import Reach, RoleTag


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "roles.yaml"
    path.write_text(body)
    return path


# ─── The real vocabulary ──────────────────────────────────────────────────


def test_shipped_taxonomy_loads():
    roles = taxonomy.load_roles()
    assert len(roles) >= 40, "vocabulary should span the domains in the plan"
    assert all(isinstance(r, RoleTag) for r in roles)


def test_shipped_taxonomy_has_exemplars():
    # Exemplars steer the model harder than definitions; a role without them
    # is usable but weak. Guard against them being dropped wholesale.
    roles = taxonomy.load_roles()
    without = [r.id for r in roles if not r.exemplars]
    assert not without, f"roles missing exemplars: {without}"


def test_prompt_block_is_byte_stable():
    # This string is the cached prefix. Instability here silently costs the
    # prompt cache on every generation call.
    assert taxonomy.as_prompt_block() == taxonomy.as_prompt_block()


def test_prompt_block_mentions_every_role():
    block = taxonomy.as_prompt_block()
    for role in taxonomy.load_roles():
        assert role.id in block


# ─── Failure paths ────────────────────────────────────────────────────────


def test_missing_file_raises(tmp_path):
    with pytest.raises(taxonomy.TaxonomyError, match="no taxonomy"):
        taxonomy.load_roles(tmp_path / "absent.yaml")


def test_malformed_yaml_raises(tmp_path):
    path = write(tmp_path, "- id: foo\n  definition: [unclosed\n")
    with pytest.raises(taxonomy.TaxonomyError, match="not valid YAML"):
        taxonomy.load_roles(path)


def test_non_list_raises(tmp_path):
    path = write(tmp_path, "id: solo\ndefinition: a role\n")
    with pytest.raises(taxonomy.TaxonomyError, match="must contain a list"):
        taxonomy.load_roles(path)


def test_empty_file_raises(tmp_path):
    path = write(tmp_path, "[]\n")
    with pytest.raises(taxonomy.TaxonomyError, match="no roles"):
        taxonomy.load_roles(path)


def test_duplicate_id_raises(tmp_path):
    path = write(
        tmp_path,
        "- id: dive_bar\n  definition: one\n- id: dive_bar\n  definition: two\n",
    )
    with pytest.raises(taxonomy.TaxonomyError, match="duplicate role id"):
        taxonomy.load_roles(path)


def test_bad_id_casing_raises(tmp_path):
    path = write(tmp_path, "- id: DiveBar\n  definition: a role\n")
    with pytest.raises(taxonomy.TaxonomyError, match="snake_case"):
        taxonomy.load_roles(path)


def test_error_names_the_offending_role(tmp_path):
    # A validation error in a 45-entry file is useless without the id.
    path = write(
        tmp_path,
        "- id: good_role\n  definition: fine\n- id: bad_role\n  exemplars: []\n",
    )
    with pytest.raises(taxonomy.TaxonomyError, match="bad_role"):
        taxonomy.load_roles(path)


# ─── Vocabulary checking ──────────────────────────────────────────────────


def test_unknown_roles_flags_drift():
    known = next(iter(taxonomy.role_ids()))
    assert taxonomy.unknown_roles([known]) == []
    assert taxonomy.unknown_roles([known, "not_a_role"]) == ["not_a_role"]


# ─── Models ───────────────────────────────────────────────────────────────


def test_reach_distinguishes_national():
    # The local/national split is what makes a chain present in both cities
    # a boring answer — it has to survive round-tripping.
    assert Reach("national") is Reach.NATIONAL
    assert Reach.LOCAL != Reach.NATIONAL
