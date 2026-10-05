"""The web adapters resolve storage, remote reads and projections through their container."""
import json
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.request import urlopen

from dependency_injector import providers

from fleet.api import Host
from fleet_web.documents import fetch_document
from fleet_web.library import ProjectLibrary
from fleet_web.server import make_handler


def test_live_state_resolves_overridden_adapters_once(cli_container):
    adapter, documents, keeper = object(), object(), object()
    cli_container.transport.override(SimpleNamespace(keep_run_trace=adapter))
    cli_container.project_documents.override(documents)
    calls = []

    def make_keeper(store, fetch, keep_trace):
        calls.append((store, fetch, keep_trace))
        return keeper

    cli_container.document_keeper.override(providers.Factory(make_keeper))
    state = cli_container.live_state(hosts=[])
    assert state.container is cli_container
    assert state.store is cli_container.store()
    assert state.documents is documents and state.keeper is keeper
    assert calls == [(documents, state.fetch_raw, state.keep_trace)]
    assert state.trace_retainer is adapter


def test_http_handler_uses_overridden_query_provider(cli_container):
    seen = []

    def project_status(*, project):
        seen.append(project)
        return {'project': project, 'work_items': [], 'attention': []}

    cli_container.project_status.override(providers.Factory(project_status))
    document_reads = []
    cli_container.run_detail.override(providers.Factory(lambda *, identity: {'run': {'id': identity}}))
    documents = SimpleNamespace(run_documents=lambda run: document_reads.append(run) or [])
    state = SimpleNamespace(container=cli_container, live_jobs=lambda: {}, documents=documents)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state))
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/bench?project=injected', timeout=5) as response:
            assert json.load(response)['rooms'] == []
        assert seen == ['injected']
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/runs/stored-run', timeout=5) as response:
            assert json.load(response)['kept_documents'] == []
        assert document_reads == [{'id': 'stored-run'}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_renderers_accept_provider_overrides_without_storage(cli_container):
    cli_container.read_document.override(providers.Factory(lambda **_: {'content': '# Injected\nFLEET_STATUS: done'}))
    rendered = fetch_document(Host('worker', None), 'job', 'report.md', container=cli_container)
    assert rendered['markdown'] == '# Injected'
    assert '<h1' in rendered['html']
    reader = SimpleNamespace(roots={'project': '/unused'},
                             read=lambda *args: {'kind': 'file', 'content': '# Local'})
    cli_container.project_library.override(reader)
    library = ProjectLibrary({'project': '/unused'}, container=cli_container)
    assert library.reader is reader
    assert library.read('project', 'report.md')['markdown'] == '# Local'


def test_fixture_scopes_isolate_paths_and_inherit_overrides(cli_container):
    adapter, query = object(), object()
    clock = lambda: datetime(2026, 10, 5, tzinfo=timezone.utc)
    cli_container.settings.override(dict(cli_container.settings(), job='parent-job', clock=clock))
    cli_container.transport.override(adapter)
    cli_container.overview.override(query)
    scopes = [cli_container.fixture_scope(), cli_container.fixture_scope()]
    try:
        first, second = [scope.container for scope in scopes]
        assert first.store() is not second.store()
        assert first.store() is first.store()
        for scope in scopes:
            container = scope.container
            settings = container.settings()
            root = Path(scope.directory.name)
            for name in ('store_path', 'config_path', 'home', 'management_home'):
                assert settings[name].is_relative_to(root)
            assert settings['job'] == 'parent-job'
            assert settings['clock'] is clock
            assert container.transport() is adapter
            assert container.overview() is query
            assert container.store().path != cli_container.settings()['store_path']
    finally:
        for scope in scopes:
            scope.directory.cleanup()


def test_handler_static_resources_can_be_overridden(cli_container):
    reads = []
    cli_container.package_resources.override(SimpleNamespace(
        read_static=lambda name: reads.append(name) or b'<html>injected</html>',
        app_files=lambda root, directories: {}))
    state = SimpleNamespace(container=cli_container)
    make_handler(state)
    assert reads == ['index.html']


def test_recorded_state_and_library_are_raw_without_web_renderers(cli_container):
    fixture = cli_container.fixture_data(path=Path(__file__).parents[1] / 'fixtures/restoke.json')
    state = cli_container.fixture_state(fixture=fixture)
    try:
        assert state.container is not cli_container
        assert 'build' not in state.document()
        key = next(iter(fixture['job_documents']))
        host, job, document = key.split('/', 2)
        raw = state.read_document(host, job, document)
        assert 'content' in raw and 'html' not in raw and 'toc' not in raw
        reader = state.container.fixture_library(fixture=fixture)
        listed = reader.list()
        assert listed
        raw = reader.read(listed[0]['project'], listed[0]['id'])
        assert 'content' in raw and 'html' not in raw
    finally:
        state.attention_directory.cleanup()
