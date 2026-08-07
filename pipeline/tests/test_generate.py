"""Offline tests for generation.

Everything here runs without API access — prompt assembly, request shape, and
result parsing are all pure. The batch itself needs a key.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from elsewhere import generate, seeds, taxonomy

# ─── Prompt assembly ──────────────────────────────────────────────────────


def test_system_prompt_is_byte_stable():
    # It's the cached prefix across every request in the batch. Any variation
    # costs the cache on all ~117 of them.
    assert generate.build_system_prompt("chicago") == generate.build_system_prompt("chicago")


def test_system_prompt_carries_full_vocabulary():
    prompt = generate.build_system_prompt("chicago")
    for role in taxonomy.load_roles():
        assert role.id in prompt, f"{role.id} missing from prompt"


def test_system_prompt_includes_candidate_pool():
    prompt = generate.build_system_prompt("chicago")
    for name in ["Mariano's", "Lou Malnati's", "The Green Mill"]:
        assert name in prompt


def test_system_prompt_does_not_restrict_to_pool():
    # Hard-constraining to the 118 curated Chicago places would cap recall at
    # whatever the curation happened to include.
    assert "NOT restricted" in generate.build_system_prompt("chicago")


def test_system_prompt_teaches_the_core_distinction():
    prompt = generate.build_system_prompt("chicago")
    assert "Jewel-Osco" in prompt and "Mariano" in prompt


def test_system_prompt_clears_cache_minimum():
    # Opus 5's minimum cacheable prefix is 512 tokens; ~4 chars/token means
    # comfortably over. Below it, caching silently does nothing.
    assert len(generate.build_system_prompt("chicago")) > 4000


def test_user_prompt_carries_aliases():
    place = next(p for p in seeds.load_seeds("austin") if p.name == "H-E-B")
    prompt = generate.build_user_prompt(place, "chicago")
    assert "H-E-B" in prompt
    assert "HEB" in prompt
    assert "Chicago" in prompt


# ─── Request construction ─────────────────────────────────────────────────


def test_requests_cover_every_seed():
    reqs = generate.build_requests("austin", "chicago")
    assert len(reqs) == len(seeds.load_seeds("austin"))


def test_custom_ids_are_unique():
    # Results come back in arbitrary order and are keyed by custom_id; a
    # collision would silently drop a match.
    ids = [r["custom_id"] for r in generate.build_requests("austin", "chicago")]
    assert len(ids) == len(set(ids))


def test_requests_enable_caching():
    req = generate.build_requests("austin", "chicago")[0]
    system = req["params"]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_requests_use_structured_output():
    req = generate.build_requests("austin", "chicago")[0]
    fmt = req["params"]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert "role_tags" in fmt["schema"]["properties"]
    assert "candidates" in fmt["schema"]["properties"]


def test_output_schema_satisfies_structured_output_constraints():
    """Regression: this shipped without `additionalProperties: false`.

    The API rejects such a schema, so every one of the 117 batch requests
    failed identically — after submission, several minutes in, with nothing
    generated. Local validation is cheap; learning it from the API is not.
    """
    assert generate.schema_problems() == []


def test_api_schema_strips_unsupported_numeric_constraints():
    """Regression: `confidence` carries ge/le, which emit minimum/maximum.

    Structured outputs rejects those, so the second batch failed exactly the
    way the first did. `messages.parse()` strips them automatically; the Batch
    API takes a hand-built schema, so we have to.
    """
    raw = generate.GeneratedMatch.model_json_schema()
    conf_raw = raw["$defs"]["GeneratedCandidate"]["properties"]["confidence"]
    assert "minimum" in conf_raw, "precondition: Pydantic still emits it"

    conf_api = generate.api_schema()["$defs"]["GeneratedCandidate"]["properties"]["confidence"]
    assert "minimum" not in conf_api and "maximum" not in conf_api


def test_stripping_constraints_does_not_weaken_validation():
    # The API no longer enforces the range, so the Pydantic model must —
    # otherwise a bad confidence would sail into the corpus.
    from pydantic import ValidationError

    payload = {
        "role_tags": ["regional_grocery_cult"],
        "price_tier": 2,
        "reach": "regional",
        "candidates": [{"name": "X", "reasoning": "y", "confidence": 4.2}],
    }
    with pytest.raises(ValidationError):
        generate.GeneratedMatch.model_validate(payload)


def test_schema_validator_catches_unsupported_keywords():
    bad = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"n": {"type": "number", "minimum": 0}},
        "required": ["n"],
    }
    assert any("unsupported keyword" in p for p in generate.schema_problems(bad))


def test_schema_validator_catches_missing_additional_properties():
    bad = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
    }
    problems = generate.schema_problems(bad)
    assert any("additionalProperties" in p for p in problems)


def test_schema_validator_catches_optional_properties():
    bad = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["a"],
    }
    problems = generate.schema_problems(bad)
    assert any("must be required" in p for p in problems)


def test_schema_validator_descends_into_nested_definitions():
    # The nested candidate object is where the real failure lived.
    bad = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
        "required": [],
        "$defs": {"Inner": {"type": "object", "properties": {"x": {"type": "string"}}}},
    }
    problems = generate.schema_problems(bad)
    assert any("Inner" in p for p in problems)


def test_preflight_refuses_a_bad_schema(monkeypatch):
    monkeypatch.setattr(generate, "schema_problems", lambda *a, **k: ["boom"])
    assert "boom" in generate.verify_ready("austin", "chicago")


def test_requests_use_opus_5():
    req = generate.build_requests("austin", "chicago")[0]
    assert req["params"]["model"] == "claude-opus-5"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("H-E-B", "h-e-b"),
        ("Torchy's Tacos", "torchy-s-tacos"),
        ("The 606", "the-606"),
    ],
)
def test_slug(name, expected):
    assert generate.slug(name) == expected


def test_slug_never_empty():
    assert generate.slug("!!!") == "unnamed"


# ─── Result parsing ───────────────────────────────────────────────────────


def test_schema_accepts_a_well_formed_response():
    payload = {
        "role_tags": ["regional_grocery_cult"],
        "price_tier": 2,
        "reach": "regional",
        "candidates": [
            {"name": "Mariano's", "reasoning": "Fills the same role.", "confidence": 0.9}
        ],
    }
    parsed = generate.GeneratedMatch.model_validate_json(json.dumps(payload))
    assert parsed.candidates[0].name == "Mariano's"


def test_schema_rejects_bad_reach():
    payload = {
        "role_tags": ["regional_grocery_cult"],
        "price_tier": 2,
        "reach": "galactic",
        "candidates": [],
    }
    with pytest.raises(ValidationError):
        generate.GeneratedMatch.model_validate_json(json.dumps(payload))


def test_schema_rejects_out_of_range_price_tier():
    payload = {
        "role_tags": [],
        "price_tier": 7,
        "reach": "local",
        "candidates": [],
    }
    with pytest.raises(ValidationError):
        generate.GeneratedMatch.model_validate_json(json.dumps(payload))


# ─── Preflight ────────────────────────────────────────────────────────────


def test_verify_ready_passes_on_the_real_corpus():
    assert generate.verify_ready("austin", "chicago") == []


def test_verify_ready_reports_missing_city():
    problems = generate.verify_ready("austin", "atlantis")
    assert problems and "atlantis" in problems[0]
