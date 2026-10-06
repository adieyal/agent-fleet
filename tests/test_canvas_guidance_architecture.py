"""Canvas persistence cannot become a second charter authority."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fleet.infrastructure.sqlite.canvas import CanvasRepository
from fleet.modules.canvas import CanvasFacade


class RecordingRepository:
    def __init__(self):
        self.writes = []

    def save(self, space, kind, identity, record, actor):
        self.writes.append(('save', kind, record))

    def present(self, space, kind, identity, record):
        self.writes.append(('present', kind, record))

    def save_version(self, space, kind, identity, version, record):
        self.writes.append(('version', kind, record))

    def last_event(self, space):
        return 0


@pytest.mark.parametrize('presentation', [False, True])
def test_canvas_commit_routes_charters_to_records_without_private_snapshots(presentation):
    """The boundary must hold even when the adapter itself accepts any kind."""
    repository = RecordingRepository()
    authored = []
    guidance = SimpleNamespace(write_space_guidance=lambda *args, **kwargs: authored.append((args, kwargs)))
    charter = {'north_star': 'One authority', 'clauses': [], 'scope': {'impl': 'tell'}}
    view = {'type': 'board', 'options': {'group': 'stage'}}
    engine = SimpleNamespace(
        changes=lambda: [('charter', 'main', charter, presentation), ('view', 'board', view, presentation)],
        versions=[('charter', 'main', 13, charter), ('view', 'board', 2, view)],
        ports=SimpleNamespace(guidance=guidance), guidance_base=12, actor='user', events=[],
    )
    facade = CanvasFacade(repository, lambda: datetime(2026, 10, 6, tzinfo=timezone.utc))
    facade.commit('project', engine)
    assert authored == [(('project', charter), {'actor': 'user', 'base': 12})]
    assert repository.writes == [('present' if presentation else 'save', 'view', view),
                                 ('version', 'view', view)]


class ForbiddenIO:
    def __getattr__(self, name):
        raise AssertionError(f'charter rejection must happen before storage I/O: {name}')


@pytest.mark.parametrize('method,arguments', [
    ('save', ('project', 'charter', 'main', {}, 'user')),
    ('present', ('project', 'charter', 'main', {})),
    ('save_version', ('project', 'charter', 'main', 1, {})),
])
def test_canvas_adapter_rejects_charter_writes_before_storage_io(method, arguments):
    repository = CanvasRepository(ForbiddenIO(), ForbiddenIO())
    with pytest.raises(ValueError, match='Fleet Records'):
        getattr(repository, method)(*arguments)
