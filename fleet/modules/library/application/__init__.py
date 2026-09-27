from uuid import uuid4

from fleet.modules.work import WorkFacade
from .ports import LibraryRepository
from ..domain import LibraryEntry, validate_external


def link(repository: LibraryRepository, work: WorkFacade, url: str, *, project: str | None,
         work_item: str | None, title: str | None, actor: str) -> LibraryEntry:
    validate_external(url)
    if not actor.strip():
        raise ValueError("actor is required")
    if work_item is not None:
        item = work.get(work_item)
        if project is not None and project != item.project:
            raise ValueError("project does not match work item")
        project = item.project
    if project is None:
        raise ValueError("project or work item is required")
    entry = LibraryEntry(str(uuid4()), project, work_item, None, "reference",
                         url if title is None else title, "linked", url, "external", True)
    repository.save(entry, actor)
    return entry
