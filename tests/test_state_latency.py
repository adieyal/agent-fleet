"""The state projection must not reread workspace state for every observed run."""
from fleet.container import Container
from fleet.services.live import FleetState
from fleet.transport import Host


def test_document_workspace_reads_do_not_scale_with_runs(monkeypatch):
    container = Container()
    state = FleetState([Host('offline', 'offline.invalid')], container=container)
    repository = state.workspace.application.repository
    original = repository.read
    reads = []

    def read():
        reads.append(None)
        return original()

    monkeypatch.setattr(repository, 'read', read)

    def document(count):
        state.by_host['offline']['jobs'] = {
            str(index): dict(id=str(index), project='unlinked', status='done', steps=[])
            for index in range(count)
        }
        reads.clear()
        result = state.document()
        return len(reads), result

    small, _ = document(1)
    large, result = document(200)
    assert large == small
    assert {job['focus'] for job in result['hosts'][0]['jobs']} == {'priority'}
    state.workspace.set_focus('background', [], ['unlinked'])
    _, updated = document(200)
    assert {job['focus'] for job in updated['hosts'][0]['jobs']} == {'background'}
