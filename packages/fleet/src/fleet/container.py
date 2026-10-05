"""Library-owned providers and transaction-local facade scopes."""
from __future__ import annotations

import os as _os
from pathlib import Path as _Path
from tempfile import TemporaryDirectory as _TemporaryDirectory
from threading import RLock as _RLock
from types import SimpleNamespace as _SimpleNamespace
from contextlib import contextmanager as _contextmanager
from typing import Callable as _Callable, Iterator as _Iterator

from fleet.services.facades import Facades as _Facades, StorePort as _StorePort, Transaction as _Transaction
from fleet.services.facades import TriagePersistence as _TriagePersistencePort, TriageScope as _TriageScope

from dependency_injector import containers as _containers
from dependency_injector import providers as _providers

from fleet import transport as _transport
from fleet.infrastructure.answers import send_answer as _send_answer
from fleet.infrastructure.documents.evidence import (
    FileEvidenceReader as _FileEvidenceReader,
)
from fleet.infrastructure.documents.job_store import (
    ProjectDocuments as _ProjectDocuments,
)
from fleet.infrastructure.documents.job_store import fleet_home as _fleet_home
from fleet.infrastructure.documents.library import ProjectLibrary as _ProjectLibrary
from fleet.infrastructure.documents.overview import OverviewCache as _OverviewCache
from fleet.infrastructure.documents.overview import files_changed as _files_changed
from fleet.infrastructure.files import LocalFiles as _LocalFiles
from fleet.infrastructure.fixtures import FixtureLibraryReader as _FixtureLibraryReader
from fleet.infrastructure.fixtures import load_fixture as _load_fixture
from fleet.infrastructure.git import RepositoryWriter as _RepositoryWriter
from fleet.infrastructure.input_delivery import send_input as _send_input
from fleet.infrastructure.job_steps import send_step as _send_step
from fleet.infrastructure.permission_grants import send_grant as _send_grant
from fleet.infrastructure.resources import PackageResources as _PackageResources
from fleet.infrastructure.sqlite import Store as _Store
from fleet.infrastructure.sqlite.attention import (
    AttentionRepository as _AttentionRepository,
)
from fleet.infrastructure.sqlite.attention_import import (
    import_workspace as _import_workspace,
)
from fleet.infrastructure.sqlite.authority import (
    AuthorityRepository as _AuthorityRepository,
)
from fleet.infrastructure.sqlite.decisions import (
    DecisionRepository as _DecisionRepository,
)
from fleet.infrastructure.sqlite.execution import (
    ExecutionRepository as _ExecutionRepository,
)
from fleet.infrastructure.sqlite.library import LibraryRepository as _LibraryRepository
from fleet.infrastructure.sqlite.migrations.project_ids import (
    migrate_project_ids as _migrate_project_ids,
)
from fleet.infrastructure.sqlite.records import RecordsRepository as _RecordsRepository
from fleet.infrastructure.sqlite.triage import TriageRepository as _TriageRepository
from fleet.infrastructure.sqlite.work import WorkRepository as _WorkRepository
from fleet.infrastructure.sqlite.workspace import (
    WorkspaceRepository as _WorkspaceRepository,
)
from fleet.ingestion import observe_runs as _observe_runs
from fleet.ingestion import observe_sessions as _observe_sessions
from fleet.ingestion import record_decisions as _record_decisions
from fleet.modules.attention import AttentionFacade as _AttentionFacade
from fleet.modules.authority import AuthorityFacade as _AuthorityFacade
from fleet.modules.decisions import DecisionsFacade as _DecisionsFacade
from fleet.modules.execution import ExecutionFacade as _ExecutionFacade
from fleet.modules.library import LibraryFacade as _LibraryFacade
from fleet.errors import FleetError as _FleetError
from fleet.modules.records import RecordsFacade as _RecordsFacade
from fleet.modules.pages import PagesFacade as _PagesFacade
from fleet.services.pages import PageService as _PageService
from fleet.modules.work import WorkFacade as _WorkFacade
from fleet.modules.workspace import WorkspaceFacade as _WorkspaceFacade
from fleet.orchestration import ControllerCommands as _ControllerCommands
from fleet.orchestration import promote_decision as _promote_decision
from fleet.projections.attention import attention_items as _attention_items
from fleet.projections.attention import decision_detail as _decision_detail
from fleet.projections.building import building_state as _building_state
from fleet.projections.decisions import decision_log as _decision_log
from fleet.projections.documents import LibraryProjection as _LibraryProjection
from fleet.projections.guidance import epic_decisions as _epic_decisions
from fleet.projections.guidance import guidance_view as _guidance_view
from fleet.projections.guidance import project_decisions as _project_decisions
from fleet.projections.history import subject_history as _subject_history
from fleet.projections.notifications import Notifications as _Notifications
from fleet.projections.overview import Overview as _Overview
from fleet.projections.ports import LiveReaders as _LiveReaders
from fleet.projections.project import project_status as _project_status
from fleet.projections.project import run_work as _run_work
from fleet.projections.project import work_detail as _project_work_detail
from fleet.projections.run_history import history_runs as _history_runs
from fleet.projections.run_history import kept_run_detail as _kept_run_detail
from fleet.projections.run_history import run_detail as _run_detail
from fleet.projections.workspace import annotate as _annotate
from fleet.services.configuration import default_actor as _default_actor
from fleet.services.configuration import validate_paths as _validate_paths
from fleet.services.documents import DocumentKeeper as _DocumentKeeper
from fleet.services.documents import read_asset as _read_asset
from fleet.services.documents import read_document as _read_document
from fleet.services.fixtures import FixtureState as _FixtureState
from fleet.services.guidance import promote_guidance as _promote_guidance
from fleet.services.guidance import write_guidance as _write_guidance
from fleet.services.hosts import merge_detected as _merge_detected
from fleet.services.jobs import listing_arguments as _listing_arguments
from fleet.services.live import FleetState as _FleetState
from fleet.services.live import start_live as _start_live
from fleet.services.storage import usage as _usage
from fleet.triage import TriageCommands as _TriageCommands
from fleet.triage_scheduler import TriageScheduler as _TriageScheduler


from fleet.identifiers import resolve_prefix as _resolve_prefix
from fleet.projections.bench import bench_rooms as _bench_rooms
from fleet.projections.bench import bench_state as _bench_state
from fleet.projections.history import parse_moment as _parse_moment
from fleet.projections.history import parse_since as _parse_since
from fleet.projections.project import filter_status as _filter_status


def store_path() -> _Path:
    return _Path(_os.environ["FLEET_STORE"]) if "FLEET_STORE" in _os.environ else _transport.config_path().parent / "fleet.db"


def management_home() -> _Path:
    return _Path(_os.environ.get("FLEET_MANAGEMENT") or _Path.home() / ".local" / "share" / "fleet" / "management")


def settings():
    return dict(store_path=store_path(), management_home=management_home(),
                config_path=_transport.config_path(), home=_fleet_home(),
                clock=None, job=_os.environ.get("FLEET_JOB_ID") or None)


_scope_lock = _RLock()


class Services:
    """Lazy, typed capabilities assembled only by the composition root."""

    def __init__(self, *,
                 store: _Callable[[], _StorePort],
                 unit: _Callable[[], _Transaction | None],
                 attention: _Callable[[], _AttentionFacade],
                 authority: _Callable[[], _AuthorityFacade],
                 decisions: _Callable[[], _DecisionsFacade],
                 execution: _Callable[[], _ExecutionFacade],
                 library: _Callable[[], _LibraryFacade],
                 records: _Callable[[], _RecordsFacade],
                 work: _Callable[[], _WorkFacade],
                 workspace: _Callable[[], _WorkspaceFacade],
                 triage_repository: _Callable[[], _TriagePersistencePort],
                 bind: _Callable[[_Transaction], _Facades]) -> None:
        self._store = store
        self._unit = unit
        self._attention = attention
        self._authority = authority
        self._decisions = decisions
        self._execution = execution
        self._library = library
        self._records = records
        self._work = work
        self._workspace = workspace
        self._triage_repository = triage_repository
        self._bind = bind

    @property
    def store(self) -> _StorePort:
        return self._store()

    @property
    def unit(self) -> _Transaction | None:
        return self._unit()

    @property
    def attention(self) -> _AttentionFacade:
        return self._attention()

    @property
    def authority(self) -> _AuthorityFacade:
        return self._authority()

    @property
    def decisions(self) -> _DecisionsFacade:
        return self._decisions()

    @property
    def execution(self) -> _ExecutionFacade:
        return self._execution()

    @property
    def library(self) -> _LibraryFacade:
        return self._library()

    @property
    def records(self) -> _RecordsFacade:
        return self._records()

    @property
    def work(self) -> _WorkFacade:
        return self._work()

    @property
    def workspace(self) -> _WorkspaceFacade:
        return self._workspace()

    @property
    def triage_repository(self) -> _TriagePersistencePort:
        return self._triage_repository()

    def bound(self, unit: _Transaction) -> _Facades:
        return self._bind(unit)

    def routing_history(self, context):
        import json

        from fleet.modules.attention import RoutingHistory
        run = self.execution.find_run(context.host, context.owner_id)
        action = None if run is None else self.execution.get_action(run.action)
        is_triage = bool(action and action.activation and
                         self.authority.get(action.activation).role == 'triage')
        retries = []
        for decision in self.decisions.list():
            if decision.activation is None:
                continue
            try:
                evidence = json.loads(decision.context)
            except (ValueError, TypeError):
                continue  # Earlier decisions have plain prose, not triage command evidence.
            if not isinstance(evidence, dict) or evidence.get('command') != 'retry':
                continue
            same_subject = (evidence.get('retry_action') == action.id
                            if action is not None and evidence.get('retry_action') is not None
                            else evidence.get('subject') == f'job:{context.host}:{context.owner_id}')
            if (same_subject and evidence.get('project') == context.project_id
                    and evidence.get('step') == context.step):
                retries.append(decision.id)
        return RoutingHistory(is_triage, tuple(retries))


def make_services(container) -> Services:
    return Services(store=container.store, unit=container.unit,
                    attention=container.attention, authority=container.authority,
                    decisions=container.decisions, execution=container.execution,
                    library=container.library, records=container.records,
                    work=container.work, workspace=container.workspace,
                    triage_repository=container.triage_persistence,
                    bind=lambda unit: bound_services(container, unit))


class TriagePersistence:
    """Bind triage records and facade capabilities within one atomic scope."""

    def __init__(self, repository: _TriageRepository, bind: _Callable[[_Transaction], _Facades]) -> None:
        self.repository, self.bind = repository, bind

    def get(self, project: str) -> dict:
        return self.repository.get(project)

    def projects(self) -> list[str]:
        return self.repository.projects()

    @_contextmanager
    def transaction(self) -> _Iterator[_TriageScope]:
        with self.repository.transaction(prepare=self.bind) as repository:
            yield _TriageScope(records=repository, services=self.bind(repository.unit))


def bound_services(container, unit):
    if hasattr(unit, '_facades'):
        if unit.store is not container.store():
            raise ValueError("unit belongs to a different store")
        return unit._facades
    with _scope_lock:
        if unit.store is not container.store():
            raise ValueError("unit belongs to a different store")
        if not hasattr(unit, '_facades'):
            scope = Container()
            for name in ('settings', 'store', 'transport', 'evidence', 'repository_writer', 'project_documents',
                         'send', 'grant', 'answer', 'step'):
                getattr(scope, name).override(getattr(container, name))
            for name, provider in container.providers.items():
                if name != 'unit' and provider.overridden and not getattr(scope, name).overridden:
                    getattr(scope, name).override(provider.last_overriding)
            scope.unit.override(_providers.Object(unit))
            unit._container = scope
            unit._facades = scope.services()
        return unit._facades


def configured_container(store=None, unit=None, *, path=None, clock=None, job=None):
    if store is not None:
        with _scope_lock:
            if not hasattr(store, '_facades'):
                container = Container()
                container.store.override(_providers.Object(store))
                store._container = container
                store._facades = container.services()
            container = store._container
            if unit is None:
                return container
            bound_services(container, unit)
            return unit._container
    container = Container()
    if path is not None or clock is not None or job is not None:
        values = dict(container.settings())
        if path is not None:
            values['store_path'] = path
        if clock is not None:
            values['clock'] = clock
        if job is not None:
            values['job'] = job
        container.settings.override(values)
    return container


def fixture_scope(parent):
    directory = _TemporaryDirectory(prefix='fleet-fixture-')
    root = _Path(directory.name)
    container = Container()
    values = dict(parent.settings(), store_path=root / 'fleet.db', config_path=root / 'config.json',
                  home=root, management_home=root / 'management')
    (root / 'config.json').write_text('{"hosts": {}}')
    container.settings.override(values)
    container.transport.override(parent.transport)
    for name, provider in parent.providers.items():
        if name not in ('settings', 'store', 'unit', 'transport') and provider.overridden:
            getattr(container, name).override(provider.last_overriding)
    return _SimpleNamespace(container=container, directory=directory)


def make_store(container, settings):
    store = _Store(settings['store_path'], settings['clock'], settings['job'])
    store._container = container
    store._facades = container.services()
    return store


def make_records(repository, writer, workspace, services, management):
    records = _RecordsFacade(repository, writer, workspace, lambda: services.work, management)
    records.decision_source = lambda: services.decisions.list()
    return records


def callback(services, name):
    return lambda: getattr(services, name)


def bound_callback(services, name):
    return lambda unit: getattr(services.bound(unit), name)


def decision_repository(services, unit):
    return services.bound(unit).decisions.repository


def collaborators(services, unit):
    bound = services.bound(unit)
    return bound.work, bound.workspace


def prepare_dispatch(container):
    services = container.services()
    return None if services.unit is not None else lambda: initialize_workspace(container)


def initialize_attention(container, *, workspace_path=None):
    services = container.services()
    _import_workspace(services.store, workspace_path if workspace_path is not None else container.settings()['config_path'].parent / 'workspace.json')
    return services.attention


def initialize_workspace(container, *, initial=None, actor="user"):
    services = container.services()
    path = container.settings()['config_path']
    container.workspace_repository().initialize(path, path.parent / 'workspace.json', initial)
    if actor == 'user':
        return services.workspace
    return container.workspace_actor(actor=actor)


def deliver_triage(container, run, *, reconcile=False):
    services = container.services()
    adapter = container.transport()
    host = adapter.host_by_name(run.host)
    return services.execution.deliver(run,
        lambda arguments, stdin: adapter.call(host, arguments, stdin_text=stdin),
        lambda job, paths, guidance: None, reconcile=reconcile)


def schedule_triage(container):
    container.triage_scheduler(deliver=container.deliver_triage).schedule()


def _make_references(container):
    services = container.services()
    from fleet.services.references import References
    return References(services, container.transport(), lambda: initialize_workspace(container), lambda: initialize_attention(container))


def _make_context(container):
    services = container.services()
    from fleet.services.context import Context
    return Context(services.records, container.transport(), container.files())


def _make_jobs(container):
    services = container.services()
    from fleet.services.jobs import Jobs
    return Jobs(services, container.transport(), lambda: initialize_workspace(container), lambda: initialize_attention(container),
                container.references(), container.context())


def _make_hosts(container):
    from fleet.services.hosts import HostSetup
    return HostSetup(container.transport())


def _make_dispatch(container):
    services = container.services()
    from fleet.services.dispatch import Dispatch
    return Dispatch(services, container.transport(), lambda: initialize_workspace(container), container.references(),
                    container.context(), lambda activation: container.controller_commands(activation))


def _make_projects(container):
    services = container.services()
    from fleet.services.projects import Projects
    return Projects(lambda: initialize_workspace(container), services.execution, services.records, container.documents, container.transport())


def _make_configuration(container):
    from fleet.services.configuration import Configuration
    return Configuration(container.transport(), lambda: initialize_workspace(container))


def _make_work_commands(container):
    services = container.services()
    from fleet.services.work import WorkCommands
    return WorkCommands(services.work, lambda: initialize_workspace(container), container.references())


def _make_triage_policy(container):
    services = container.services()
    from fleet.services.triage import TriagePolicy
    return TriagePolicy(services, lambda: initialize_workspace(container),
                        container.triage_scheduler)


def _make_attention_commands(container):
    services = container.services()
    from fleet.services.attention import AttentionCommands
    return AttentionCommands(services, lambda: initialize_workspace(container), lambda: initialize_attention(container),
                             container.references())


def _make_decision_commands(container):
    services = container.services()
    from fleet.services.decisions import DecisionCommands
    return DecisionCommands(services, lambda: initialize_workspace(container), container.references(), container.transport())


def _make_history(services):
    from fleet.services.history import History
    return History(services.store)


def resolved_work_detail(container, reference):
    identity = container.references().work(reference)
    initialize_attention(container)
    return container.work_detail(identity=identity)


def make_live_readers(container, workspace: _WorkspaceFacade, attention: _AttentionFacade) -> _LiveReaders:
    store = container.store()
    return _LiveReaders(attention=attention, execution=container.execution(), workspace=workspace,
                        revision=lambda: (store, store.latest_sequence()),
                        run_work=container.run_work, building=container.building_state,
                        history_runs=container.history_runs, run_detail=container.run_detail,
                        overview=container.overview())


def page_query(container, project, slug=None, revision=None):
    with container.unit_of_work() as unit:
        services = container.bound_services(unit)
        service = _PageService(services, container.pages())
        return service.index(project) if slug is None else service.read(project, slug, revision)


def page_command(container, operation, project, slug=None, **fields):
    with container.unit_of_work() as unit:
        service = _PageService(container.bound_services(unit), container.pages())
        try:
            if operation == 'write':
                return service.write(project, slug, **fields)
            if operation == 'show':
                return service.show(project, slug, **fields)
            if operation == 'ls':
                return service.index(project)
            raise ValueError('unknown page command')
        except (ValueError, LookupError) as error:
            raise _FleetError(str(error)) from error


def page_change(container, project, slug, operation, **fields):
    with container.unit_of_work() as unit:
        service = _PageService(container.bound_services(unit), container.pages())
        if operation == 'comment':
            return service.comment(project, slug, **fields)
        if operation == 'answer':
            return service.answer(project, slug, **fields)
        raise ValueError('unknown page operation')


class Container(_containers.DeclarativeContainer):
    resolve_prefix = _providers.Callable(_resolve_prefix)
    validate_paths = _providers.Callable(_validate_paths)
    default_actor = _providers.Callable(_default_actor)
    listing_arguments = _providers.Callable(_listing_arguments)
    merge_detected = _providers.Callable(_merge_detected)
    parse_moment = _providers.Callable(_parse_moment)
    parse_since = _providers.Callable(_parse_since)
    filter_status = _providers.Callable(_filter_status)
    bench_rooms = _providers.Callable(_bench_rooms)
    bench_state = _providers.Callable(_bench_state)
    __self__ = _providers.Self()
    settings = _providers.ThreadSafeSingleton(settings)
    transport = _providers.Object(_transport)
    unit = _providers.Object(None)
    store = _providers.ThreadSafeSingleton(make_store, __self__, settings)
    services = _providers.ThreadSafeSingleton(make_services, __self__)
    unit_of_work = _providers.Factory(lambda store: store.unit_of_work(), store)
    bound_services = _providers.Callable(bound_services, __self__)
    send = _providers.Object(_send_input)
    grant = _providers.Object(_send_grant)
    answer = _providers.Object(_send_answer)
    step = _providers.Object(_send_step)
    evidence = _providers.ThreadSafeSingleton(_FileEvidenceReader)
    repository_writer = _providers.ThreadSafeSingleton(_RepositoryWriter)
    files = _providers.ThreadSafeSingleton(_LocalFiles)
    _document_root = _providers.Callable(lambda settings: settings['home'] / 'projects', settings)
    project_documents = _providers.ThreadSafeSingleton(_ProjectDocuments, _document_root)
    _attention_repository = _providers.ThreadSafeSingleton(_AttentionRepository, store, unit)
    workspace_repository = _providers.ThreadSafeSingleton(_WorkspaceRepository, store, unit)
    triage_repository = _providers.ThreadSafeSingleton(_TriageRepository, store, unit)
    triage_persistence = _providers.ThreadSafeSingleton(TriagePersistence, triage_repository, bound_services.provider)
    _records_repository = _providers.ThreadSafeSingleton(_RecordsRepository, store, unit)
    _authority_repository = _providers.ThreadSafeSingleton(_AuthorityRepository, store, unit)
    _library_repository = _providers.ThreadSafeSingleton(_LibraryRepository, store, unit)
    _attention_bound = _providers.Callable(bound_callback, services, 'attention')
    _work_bound = _providers.Callable(bound_callback, services, 'work')
    _execution_bound = _providers.Callable(bound_callback, services, 'execution')
    _records_bound = _providers.Callable(bound_callback, services, 'records')
    _work_repository = _providers.ThreadSafeSingleton(_WorkRepository, store, _attention_bound, unit)
    _execution_repository = _providers.ThreadSafeSingleton(_ExecutionRepository, store, unit,
        attention=_attention_bound,
        decisions=_providers.Callable(lambda services: lambda unit: decision_repository(services, unit), services),
        collaborators=_providers.Callable(lambda services: lambda unit: collaborators(services, unit), services))
    _decisions_repository = _providers.ThreadSafeSingleton(_DecisionRepository, store,
        _attention_bound, _work_bound, _execution_bound, records=_records_bound, unit=unit)
    workspace = _providers.ThreadSafeSingleton(_WorkspaceFacade, workspace_repository)
    workspace_actor = _providers.Factory(_WorkspaceFacade, workspace_repository)
    records = _providers.ThreadSafeSingleton(make_records, _records_repository, repository_writer,
        workspace, services, settings.provided['management_home'])
    attention = _providers.ThreadSafeSingleton(_AttentionFacade, _attention_repository, store.provided.clock,
        mandate=_providers.Callable(lambda services: lambda project: services.records.triage_mandate(project), services),
        routing_history=services.provided.routing_history)
    work = _providers.ThreadSafeSingleton(_WorkFacade, _work_repository, evidence, store.provided.clock,
        records=records, authority=_providers.Callable(callback, services, 'authority'))
    execution = _providers.ThreadSafeSingleton(_ExecutionFacade, _execution_repository, work,
        decision_source=_providers.Callable(lambda services: lambda: services.decisions.list(), services),
        send=send, grant=grant, answer=answer, step=step,
        prepare_dispatch=_providers.Callable(prepare_dispatch, __self__),
        authority=_providers.Callable(callback, services, 'authority'), clock=store.provided.clock)
    decisions = _providers.ThreadSafeSingleton(_DecisionsFacade, _decisions_repository, store.provided.clock,
        execution, records=records, authority=_providers.Callable(callback, services, 'authority'))
    authority = _providers.ThreadSafeSingleton(_AuthorityFacade, _authority_repository,
        records, work, decisions, attention, execution)
    library = _providers.ThreadSafeSingleton(_LibraryFacade, _library_repository, work)
    pages = _providers.ThreadSafeSingleton(_PagesFacade)
    page_command = _providers.Callable(page_command, __self__)
    page_view = _providers.Callable(page_query, __self__)
    page_change = _providers.Callable(page_change, __self__)
    references = _providers.Factory(_make_references, __self__)
    context = _providers.Factory(_make_context, __self__)
    jobs = _providers.Factory(_make_jobs, __self__)
    dispatch = _providers.Factory(_make_dispatch, __self__)
    projects = _providers.Factory(_make_projects, __self__)
    configuration = _providers.Factory(_make_configuration, __self__)
    work_commands = _providers.Factory(_make_work_commands, __self__)
    triage_policy = _providers.Factory(_make_triage_policy, __self__)
    attention_commands = _providers.Factory(_make_attention_commands, __self__)
    decision_commands = _providers.Factory(_make_decision_commands, __self__)
    history = _providers.Factory(_make_history, services)
    hosts = _providers.Factory(_make_hosts, __self__)
    controller_commands = _providers.Factory(_ControllerCommands, services)
    triage_commands = _providers.Factory(_TriageCommands, services)
    triage_scheduler = _providers.Factory(_TriageScheduler, services, deliver=None,
        host=transport.provided.host_by_name)
    notifications = _providers.Factory(_Notifications)
    attention_items = _providers.Factory(_attention_items, attention=attention)
    building_state = _providers.Factory(_building_state, workspace=workspace)
    decision_log = _providers.Factory(_decision_log, work=work, decisions=decisions)
    subject_history = _providers.Factory(_subject_history, store=store)
    run_work = _providers.Factory(_run_work, work=work, execution=execution)
    project_status = _providers.Factory(_project_status, work=work, attention=attention,
        execution=execution, library=library, decisions=decisions)
    work_detail = _providers.Factory(_project_work_detail, work=work, attention=attention,
        execution=execution, library=library, decisions=decisions)
    history_runs = _providers.Factory(_history_runs, execution=execution, work=work, workspace=workspace)
    run_detail = _providers.Factory(_run_detail, execution=execution, work=work, library=library)
    kept_run_detail = _providers.Factory(_kept_run_detail, execution=execution, work=work,
        library=library, documents=project_documents)
    annotate_workspace = _providers.Factory(_annotate, workspace=workspace)
    documents = _providers.Factory(_ProjectDocuments, root=_document_root)
    document_keeper = _providers.Factory(_DocumentKeeper)
    initialized_workspace = _providers.Callable(initialize_workspace, __self__)
    initialized_attention = _providers.Callable(initialize_attention, __self__)
    migrate_project_ids = _providers.Callable(_migrate_project_ids, store=store, workspace=initialized_workspace)
    storage_usage = _providers.Factory(_usage, store=store, root=settings.provided['home'], files=files)
    resolved_work_detail = _providers.Factory(resolved_work_detail, __self__)
    promote_decision = _providers.Factory(_promote_decision, services)
    fixture_scope = _providers.Callable(fixture_scope, __self__)
    project_library = _providers.Factory(_ProjectLibrary)
    overview_cache = _providers.ThreadSafeSingleton(_OverviewCache)
    overview = _providers.Factory(_Overview, cache=overview_cache, files_changed=_files_changed)
    read_document = _providers.Callable(_read_document, adapter=transport)
    read_asset = _providers.Callable(_read_asset, adapter=transport)
    deliver_triage = _providers.Callable(deliver_triage, __self__)
    schedule_triage = _providers.Callable(schedule_triage, __self__)
    observe_runs = _providers.Callable(_observe_runs, execution, library)
    observe_sessions = _providers.Callable(_observe_sessions, execution)
    record_decisions = _providers.Callable(_record_decisions, decisions, execution, attention)

    package_resources = _providers.Factory(_PackageResources)
    live_history_detail = _providers.Callable(_LibraryProjection.history_detail)
    live_history_runs = _providers.Callable(_LibraryProjection.history_runs)
    live_readers = _providers.Factory(make_live_readers, __self__, workspace=workspace, attention=attention)
    live_state = _providers.Factory(_FleetState, container=__self__)
    start_live = _providers.Callable(_start_live)
    fixture_data = _providers.Callable(_load_fixture)
    fixture_state = _providers.Factory(_FixtureState, container=__self__)
    fixture_library = _providers.Factory(lambda fixture, project_library: _FixtureLibraryReader(
        fixture, project_library(roots=fixture.get('library_roots', {}))), project_library=project_library.provider)
    guidance_view = _providers.Callable(_guidance_view, work=work, records=records)
    epic_decisions = _providers.Callable(_epic_decisions, work=work, records=records, decisions=decisions)
    project_decisions = _providers.Callable(_project_decisions, work=work, decisions=decisions)
    decision_detail = _providers.Callable(_decision_detail)
    write_guidance = _providers.Callable(_write_guidance, services=services)
    promote_guidance = _providers.Callable(_promote_guidance, services=services, promote=promote_decision.provider)

__all__ = ["Container"]
