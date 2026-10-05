"""Context workflows and storage aggregation use injected file operations."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fleet.errors import FleetError
from fleet.services.context import Context
from fleet.services.storage import usage


def host(local):
    return SimpleNamespace(name='worker', is_local=local, rsync_target=lambda path: f'target:{path}')


@pytest.mark.parametrize('local', [False, True])
def test_context_push_validates_then_addresses_sources(local):
    files, adapter = Mock(), Mock()
    files.exists.return_value = True
    files.absolute.side_effect = lambda path: '/absolute/' + path
    files.expand_user.return_value = '/home/worker/.fleet/jobs/j/context/'
    service = Context(Mock(), adapter, files)
    target_host = host(local)
    service.push(target_host, 'j', ['one', 'two'])
    directory = '/home/worker/.fleet/jobs/j/context/' if local else '~/.fleet/jobs/j/context/'
    adapter.rsync.assert_called_once_with(['/absolute/one', '/absolute/two'], 'target:' + directory,
                                          target_host)
    assert [call.args for call in files.exists.call_args_list] == [('one',), ('two',)]
    files.exists.side_effect = lambda path: path != 'two'
    with pytest.raises(FleetError, match='context not found: two'):
        service.push(host(local), 'j', ['one', 'two'])
    assert adapter.rsync.call_count == 1


def test_guided_context_owns_temporary_directory_even_when_transfer_fails():
    events = []
    files, records, adapter = Mock(), Mock(), Mock()

    @contextmanager
    def temporary(*, prefix):
        assert prefix == 'fleet-guidance-'
        events.append('open')
        try:
            yield '/export'
        finally:
            events.append('close')
    files.temporary_directory.side_effect = temporary
    files.exists.return_value = True
    files.absolute.side_effect = lambda path: path
    records.write_guidance_files.return_value = ['/export/CONSTITUTION.md']
    adapter.rsync.side_effect = FleetError('transfer failed')
    guidance = {'project': 'p'}
    with pytest.raises(FleetError, match='transfer failed'):
        Context(records, adapter, files).push_guided(host(False), 'j', ['context'], guidance)
    records.write_guidance_files.assert_called_once_with(guidance, '/export')
    assert adapter.rsync.call_args.args[0] == ['context', '/export/CONSTITUTION.md']
    assert events == ['open', 'close']


def test_reference_resolution_and_warning_are_service_choices():
    files, adapter = Mock(), Mock()
    adapter.configured_hosts.return_value = [host(True)]
    files.find_reference.return_value = Path('/reference.md')
    service = Context(Mock(), adapter, files)
    assert service.locate('reference.md') == ('fleet://worker/reference.md', False)
    files.find_reference.return_value = None
    assert service.locate('missing.md') == ('missing.md', True)
    assert service.locate('prose') == ('prose', False)
    files.find_reference.reset_mock()
    assert service.locate('https://example.test') == ('https://example.test', False)
    assert service.locate('session:worker:j') == ('session:worker:j', False)
    files.find_reference.assert_not_called()


@pytest.mark.parametrize('local', [False, True])
def test_pull_delegates_destination_creation_and_preserves_host_addressing(local):
    files, adapter = Mock(), Mock()
    files.create_directory.return_value = Path('/destination')
    files.expand_user.return_value = '/home/worker/.fleet/jobs/j/outbox/'
    target_host = host(local)
    assert Context(Mock(), adapter, files).pull(target_host, 'j', None) == Path('/destination')
    files.create_directory.assert_called_once_with('./fleet-j')
    source = '/home/worker/.fleet/jobs/j/outbox/' if local else '~/.fleet/jobs/j/outbox/'
    adapter.rsync.assert_called_once_with(['target:' + source], '/destination', target_host)


def test_storage_aggregates_injected_scans_and_propagates_errors():
    store, files = Mock(), Mock()
    store.usage.return_value = {'database': {'bytes': 7}}
    files.usage.side_effect = [{'files': 2, 'bytes': 9}, {'files': 1, 'bytes': 4}]
    assert usage(store, Path('/root'), files) == {'database': {'bytes': 7},
                                               'documents': {'files': 2, 'bytes': 9},
                                               'traces': {'files': 1, 'bytes': 4}}
    assert [call.args for call in files.usage.call_args_list] == [(Path('/root/projects'),), (Path('/root/traces'),)]
    files.usage.side_effect = OSError('scan failed')
    with pytest.raises(OSError, match='scan failed'):
        usage(store, Path('/root'), files)
