from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from dependency_injector import providers


from fleet.container import configured_container
from fleet.container import Container


def test_store_is_lazy_shared_concurrently_and_local_to_container(tmp_path):
    first, second = Container(), Container()
    settings = dict(first.settings(), store_path=tmp_path / 'new.db')
    first.settings.override(settings)
    second.settings.override(dict(settings, store_path=tmp_path / 'other.db'))
    assert not settings['store_path'].exists()
    with ThreadPoolExecutor(max_workers=4) as executor:
        stores = list(executor.map(lambda _: first.store(), range(12)))
    assert all(store is stores[0] for store in stores)
    assert second.store() is not stores[0]
    assert configured_container(stores[0]).services() is first.services()


def test_settings_clock_job_and_management_are_injected(tmp_path):
    container = Container()
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    container.settings.override(dict(container.settings(), store_path=tmp_path / 'clock.db',
                                     clock=lambda: now, job='test-job',
                                     management_home=tmp_path / 'management'))
    assert container.store().clock() == now
    assert container.store().job == 'test-job'
    container.work().add(project='p', title='Injected clock', goal='Test', actor='test')
    entry = container.store().history_after(0)[0]
    assert container.records().home == tmp_path / 'management'
    assert entry['time'] == now.isoformat()
    assert entry['job'] == 'test-job'


def test_provider_overrides_reach_services_and_bound_scopes():
    container = Container()
    adapter, evidence = object(), object()
    container.transport.override(providers.Object(adapter))
    container.evidence.override(providers.Object(evidence))
    assert container.context().transport is adapter
    assert container.jobs().transport is adapter
    with container.unit_of_work() as unit:
        bound = container.bound_services(unit)
        assert configured_container(container.store(), unit).transport() is adapter
        assert configured_container(container.store(), unit).evidence() is evidence
        assert bound.work is not container.work()
    other = Container()
    fake = object()
    other.work.override(fake)
    assert other.services().work is fake
    with other.unit_of_work() as unit:
        assert other.bound_services(unit).work is fake


def test_bound_facades_share_unit_and_roll_back():
    container = Container()
    root = container.services()
    with pytest.raises(RuntimeError, match='rollback'):
        with container.unit_of_work() as unit:
            bound = root.bound(unit)
            assert bound is root.bound(unit) is container.bound_services(unit)
            assert configured_container(root.store, unit).services() is bound
            for name in ('attention', 'workspace', 'records', 'work', 'execution', 'decisions', 'authority', 'library'):
                facade = getattr(bound, name)
                assert facade is getattr(bound, name)
                assert facade is not getattr(root, name)
                repository = facade.application.repository if name == 'workspace' else facade.repository
                assert repository.unit is unit
                assert repository.store is root.store
            bound.work.add(project='p', title='Rolled back', goal='Test', actor='test')
            raise RuntimeError('rollback')
    assert root.work.list() == []
    assert root.store.latest_sequence() == 0
    with container.unit_of_work() as next_unit:
        assert root.bound(next_unit) is not bound
        root.bound(next_unit).work.add(project='p', title='Committed', goal='Test', actor='test')
    assert root.work.list()[0].title == 'Committed'


def test_rejects_unit_from_another_store(tmp_path):
    first, second = Container(), Container()
    second.settings.override(dict(second.settings(), store_path=tmp_path / 'other.db'))
    with second.unit_of_work() as unit:
        with pytest.raises(ValueError, match='different store'):
            first.bound_services(unit)


def test_document_root_and_execution_adapters_are_injected(tmp_path):
    container = Container()
    container.settings.override(dict(container.settings(), home=tmp_path / 'home'))
    assert container.documents().root == tmp_path / 'home/projects'
    assert container.project_documents().root == tmp_path / 'home/projects'
    def send(request):
        return 'sent'

    container.send.override(providers.Object(send))
    assert container.execution().send is send
    with container.unit_of_work() as unit:
        assert container.bound_services(unit).execution.send is send


def test_query_providers_share_facades_without_writes():
    container = Container()
    item = container.work().add(project='p', title='Query', goal='Test', actor='test')
    before = container.store().latest_sequence()
    projection = container.project_status(project='p')
    assert projection['work_items'][0]['id'] == item.id
    assert container.work_detail(identity=item.id)['id'] == item.id
    assert container.store().latest_sequence() == before
