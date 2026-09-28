from __future__ import annotations

from typing import Protocol

from ..domain import LibraryEntry


class LibraryRepository(Protocol):
    def save(self, entry: LibraryEntry, actor: str) -> None: ...
    def list(self) -> list[LibraryEntry]: ...
