"""Loading and validation for the role vocabulary."""

from __future__ import annotations

import functools
from pathlib import Path

import yaml

from elsewhere.models import RoleTag

REPO_ROOT = Path(__file__).resolve().parents[2]
TAXONOMY_PATH = REPO_ROOT / "data" / "taxonomy" / "roles.yaml"


class TaxonomyError(Exception):
    """Raised when the role vocabulary is malformed."""


def load_roles(path: Path | None = None) -> list[RoleTag]:
    """Parse and validate the role vocabulary.

    Fails loudly rather than skipping bad entries — a silently dropped role is
    a silently degraded match, and it would surface as an unexplained accuracy
    regression three phases later.
    """
    path = path or TAXONOMY_PATH
    if not path.exists():
        raise TaxonomyError(f"no taxonomy at {path}")

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise TaxonomyError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, list):
        raise TaxonomyError(f"{path} must contain a list of roles, got {type(raw).__name__}")

    roles = []
    for i, entry in enumerate(raw):
        try:
            roles.append(RoleTag.model_validate(entry))
        except Exception as exc:
            ident = entry.get("id", f"<entry {i}>") if isinstance(entry, dict) else f"<entry {i}>"
            raise TaxonomyError(f"invalid role {ident!r}: {exc}") from exc

    seen: set[str] = set()
    for role in roles:
        if role.id in seen:
            raise TaxonomyError(f"duplicate role id {role.id!r}")
        seen.add(role.id)

    if not roles:
        raise TaxonomyError(f"{path} contains no roles")

    return roles


@functools.cache
def role_ids() -> frozenset[str]:
    """Valid role ids, for checking model output against the vocabulary."""
    return frozenset(r.id for r in load_roles())


def unknown_roles(tags: list[str]) -> list[str]:
    """Return tags not present in the vocabulary.

    Generation constrains role tags via structured output, but this catches
    drift if the taxonomy is edited after a corpus was generated.
    """
    known = role_ids()
    return [t for t in tags if t not in known]


def as_prompt_block(path: Path | None = None) -> str:
    """Render the vocabulary for the generation system prompt.

    This string is the cached prefix, so it must be byte-stable across calls —
    any variation here silently costs the cache. Roles are emitted in file
    order rather than sorted, so reordering the YAML is a deliberate act.
    """
    lines = []
    for role in load_roles(path):
        definition = " ".join(role.definition.split())
        lines.append(f"- {role.id}: {definition}")
        if role.exemplars:
            lines.append(f"  e.g. {'; '.join(role.exemplars)}")
    return "\n".join(lines)
