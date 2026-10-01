"""Unique-prefix selection shared by CLI and read-only HTTP projections."""

from fleet.errors import FleetError


def resolve_prefix(reference: str, identities: list[str], kind: str) -> str:
    """Expand a unique prefix without changing canonical IDs in module APIs or JSON."""
    if reference in identities:
        return reference
    matches = sorted(identity for identity in identities if reference and identity.startswith(reference))
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise FleetError(f"ambiguous {kind} '{reference}': {', '.join(matches)}; give a longer ID")
    remedy = "fleet status PROJECT lists work items and criteria" if kind != "attention item" else "fleet attention list lists IDs"
    raise FleetError(f"no {kind} '{reference}'; {remedy}")

