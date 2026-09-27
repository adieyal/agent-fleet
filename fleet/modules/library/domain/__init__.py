"""Canonical index references; indexing grants no access."""

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class LibraryEntry:
    id: str
    project: str
    work_item: str | None
    run: str | None
    kind: str
    title: str | None
    source: str
    canonical_location: str
    availability: str
    current: bool

    def __post_init__(self) -> None:
        for field in ("project", "kind", "source", "canonical_location"):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} is required")
        if self.availability not in ("available", "unavailable", "external"):
            raise ValueError("unknown library availability")


def validate_external(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("external reference requires an HTTP or HTTPS URL")
