from uuid import NAMESPACE_URL, uuid4, uuid5

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
                         title, "linked", url, "external", True)
    repository.save(entry, actor)
    return entry


def index_run(repository: LibraryRepository, work: WorkFacade, *, run: str, work_item: str,
              kind: str, title: str | None, location: str, availability: str) -> LibraryEntry:
    item = work.get(work_item)
    identity = str(uuid5(NAMESPACE_URL, f"{run}:{kind}:{location}"))
    entry = LibraryEntry(identity, item.project, work_item, run, kind, title, "fleetd",
                         location, availability, True)
    repository.save(entry, "fleetd")
    return entry
