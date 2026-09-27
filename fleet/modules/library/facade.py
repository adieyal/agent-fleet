from __future__ import annotations

from fleet.modules.work import WorkFacade
from .application import link
from .application.ports import LibraryRepository
from .domain import LibraryEntry


class LibraryFacade:
    def __init__(self, repository: LibraryRepository, work: WorkFacade) -> None:
        self.repository, self.work = repository, work

    def link(self, url: str, *, actor: str, project: str | None = None,
             work_item: str | None = None, title: str | None = None) -> LibraryEntry:
        return link(self.repository, self.work, url, project=project, work_item=work_item, title=title, actor=actor)

    def list(self) -> list[LibraryEntry]:
        return self.repository.list()
