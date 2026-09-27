from types import SimpleNamespace

import pytest

from fleet.modules.library import LibraryFacade


def test_external_index_requires_scope_and_never_needs_a_fetch_port():
    saved = []
    repository = SimpleNamespace(save=lambda entry, actor: saved.append((entry, actor)))
    work = SimpleNamespace(get=lambda identity: SimpleNamespace(project="p"))
    library = LibraryFacade(repository, work)
    entry = library.link("https://example.org/doc", work_item="work", title="Report", actor="user")
    assert (entry.project, entry.title, entry.current, entry.run) == ("p", "Report", True, None)
    assert saved == [(entry, "user")]
    for options, message in [({}, "project or work item"),
                             ({"work_item": "work", "project": "other"}, "does not match")]:
        with pytest.raises(ValueError, match=message):
            library.link("https://example.org/doc", actor="user", **options)
    with pytest.raises(ValueError, match="HTTP"):
        library.link("file:///private", project="p", actor="user")
    assert len(saved) == 1


def test_omitted_title_stays_unknown():
    saved = []
    repository = SimpleNamespace(save=lambda entry, actor: saved.append((entry, actor)))
    library = LibraryFacade(repository, SimpleNamespace())
    entry = library.link("https://example.org/doc", project="p", actor="user")
    assert entry.title is None
    assert saved == [(entry, "user")]
