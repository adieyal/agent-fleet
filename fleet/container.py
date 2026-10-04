"""Library-owned providers and transaction-local facade scopes."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Callable

from dependency_injector import containers, providers

from fleet import transport
from fleet.infrastructure.sqlite import Store
from fleet.infrastructure.sqlite.attention import AttentionRepository
from fleet.infrastructure.sqlite.attention_import import import_workspace
from fleet.modules.attention import AttentionFacade
from fleet.modules.work import WorkFacade
from fleet.infrastructure.sqlite.work import WorkRepository
from fleet.infrastructure.documents.evidence import FileEvidenceReader
from fleet.infrastructure.sqlite.workspace import WorkspaceRepository
from fleet.modules.workspace import WorkspaceFacade
from fleet.infrastructure.sqlite.execution import ExecutionRepository
from fleet.infrastructure.sqlite.library import LibraryRepository
from fleet.modules.execution import ExecutionFacade
from fleet.modules.library import LibraryFacade
from fleet.modules.decisions import DecisionsFacade
from fleet.infrastructure.sqlite.decisions import DecisionRepository
from fleet.infrastructure.input_delivery import send_input
from fleet.infrastructure.answers import send_answer
from fleet.infrastructure.permission_grants import send_grant
from fleet.infrastructure.job_steps import send_step
from fleet.infrastructure.sqlite.records import RecordsRepository
from fleet.infrastructure.git import RepositoryWriter
from fleet.modules.records import RecordsFacade
from fleet.modules.authority import AuthorityFacade
from fleet.infrastructure.sqlite.authority import AuthorityRepository
from fleet.infrastructure.sqlite.triage import TriageRepository
from fleet.infrastructure.documents.job_store import ProjectDocuments, fleet_home
from fleet.services.documents import DocumentKeeper, STATUS_LINE, is_private
from fleet.ingestion import observe_runs, observe_sessions, record_decisions
from fleet.orchestration import ControllerCommands
from fleet.triage import TriageCommands
from fleet.triage_scheduler import TriageScheduler
from fleet.projections.attention import attention_items
from fleet.projections.building import building_state
from fleet.projections.decisions import decision_log
from fleet.projections.history import subject_history
from fleet.projections.notifications import Notifications
from fleet.projections.project import project_status, work_detail as project_work_detail
from fleet.projections.run_history import history_runs, kept_run_detail
from fleet.projections.workspace import annotate


def store_path() -> Path:
    return Path(os.environ["FLEET_STORE"]) if "FLEET_STORE" in os.environ else transport.config_path().parent / "fleet.db"


def management_home() -> Path:
    return Path(os.environ.get("FLEET_MANAGEMENT") or Path.home() / ".local" / "share" / "fleet" / "management")


def settings():
    return dict(store_path=store_path(), management_home=management_home(),
                config_path=transport.config_path(), home=fleet_home(),
                clock=None, job=os.environ.get("FLEET_JOB_ID") or None)


_scope_lock = RLock()


class Services:
    def __init__(self, container):
        self.container = container

    @property
    def store(self):
        return self.container.store()

    @property
    def unit(self):
        return self.container.unit()

    def __getattr__(self, name):
        return getattr(self.container, name)()

    def bound(self, unit):
        return bound_services(self.container, unit)

    def routing_history(self, context):
        from fleet.modules.attention import RoutingHistory
        import json
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


def bound_services(container, unit):
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
            scope.unit.override(providers.Object(unit))
            unit._facades = scope.services()
        return unit._facades


def facades(store=None, unit=None):
    if store is None:
        return Container().services()
    with _scope_lock:
        if not hasattr(store, '_facades'):
            container = Container()
            container.store.override(providers.Object(store))
            store._facades = container.services()
        services = store._facades
        return services if unit is None else services.bound(unit)


def make_store(container, settings):
    store = Store(settings['store_path'], settings['clock'], settings['job'])
    store._facades = container.services()
    return store


def make_records(repository, writer, workspace, services, management):
    records = RecordsFacade(repository, writer, workspace, lambda: services.work, management)
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


def prepare_dispatch(services):
    return None if services.unit is not None else lambda: initialize_workspace(services)


def open_attention(store: Store | None = None, *, workspace_path: Path | None = None) -> AttentionFacade:
    return initialize_attention(facades(store), workspace_path=workspace_path)


def initialize_attention(services, *, workspace_path=None):
    import_workspace(services.store, workspace_path if workspace_path is not None else services.container.settings()['config_path'].parent / 'workspace.json')
    return services.attention


def open_work(store: Store | None = None) -> WorkFacade:
    return facades(store).work


def open_records(store: Store | None = None) -> RecordsFacade:
    return facades(store).records


def open_workspace(store: Store | None = None, *, initial: dict | None = None,
                   actor: str = 'user') -> WorkspaceFacade:
    return initialize_workspace(facades(store), initial=initial, actor=actor)


def initialize_workspace(services, *, initial=None, actor="user"):
    path = services.container.settings()['config_path']
    services.workspace_repository.initialize(path, path.parent / 'workspace.json', initial)
    if actor == 'user':
        return services.workspace
    return services.container.workspace_actor(actor=actor)


def open_execution(store: Store | None = None) -> ExecutionFacade:
    return facades(store).execution


def storage_usage(store: Store | None = None) -> dict:
    from fleet.services.storage import usage
    root = Path(os.environ.get("FLEET_HOME") or "~/.fleet").expanduser()
    return usage(store if store is not None else Container().store(), root)


def open_library(store: Store | None = None) -> LibraryFacade:
    return facades(store).library


def open_decisions(store: Store | None = None) -> DecisionsFacade:
    return facades(store).decisions


def open_authority(store: Store | None = None) -> AuthorityFacade:
    return facades(store).authority


def deliver_triage(services, run, *, reconcile=False):
    adapter = services.container.transport()
    host = adapter.host_by_name(run.host)
    return services.execution.deliver(run,
        lambda arguments, stdin: adapter.call(host, arguments, stdin_text=stdin),
        lambda job, paths, guidance: None, reconcile=reconcile)



def open_references(services):
    from fleet.services.references import References
    return References(services, services.container.transport(), lambda: initialize_workspace(services), lambda: initialize_attention(services))


def open_context(services):
    from fleet.services.context import Context
    return Context(services.records, services.container.transport())


def open_jobs(services):
    from fleet.services.jobs import Jobs
    return Jobs(services, services.container.transport(), lambda: initialize_workspace(services), lambda: initialize_attention(services),
                services.container.references(), services.container.context())


def open_hosts(services):
    from fleet.services.hosts import HostSetup
    return HostSetup(services.container.transport())


def open_dispatch(services):
    from fleet.services.dispatch import Dispatch
    return Dispatch(services, services.container.transport(), lambda: initialize_workspace(services), services.container.references(),
                    services.container.context(), lambda activation: services.container.controller_commands(activation))


def open_projects(services):
    from fleet.services.projects import Projects
    return Projects(lambda: initialize_workspace(services), services.execution, services.records, services.container.documents, services.container.transport())


def open_configuration(services):
    from fleet.services.configuration import Configuration
    return Configuration(services.container.transport(), lambda: initialize_workspace(services))


def open_work_commands(services):
    from fleet.services.work import WorkCommands
    return WorkCommands(services.work, lambda: initialize_workspace(services), services.container.references())


def open_triage_policy(services):
    from fleet.services.triage import TriagePolicy
    return TriagePolicy(services, lambda: initialize_workspace(services),
                        services.container.triage_scheduler)


def open_attention_commands(services):
    from fleet.services.attention import AttentionCommands
    return AttentionCommands(services, lambda: initialize_workspace(services), lambda: initialize_attention(services),
                             services.container.references())


def open_decision_commands(services):
    from fleet.services.decisions import DecisionCommands
    return DecisionCommands(services, lambda: initialize_workspace(services), services.container.references(), services.container.transport())


def open_history(services):
    from fleet.services.history import History
    return History(services.store)


def stored_run_detail(identity, store, documents=None):
    from fleet.projections.run_history import kept_run_detail
    services = facades(store)
    return kept_run_detail(identity, services.execution, services.work, services.library, documents or services.container.project_documents())


def work_detail(reference, store=None):
    from fleet.projections.project import work_detail
    services = facades(store)
    identity = services.container.references().work(reference)
    return work_detail(identity, services.work, open_attention(services.store), services.execution, services.library, services.decisions)


class Container(containers.DeclarativeContainer):
    __self__ = providers.Self()
    settings = providers.ThreadSafeSingleton(settings)
    transport = providers.Object(transport)
    unit = providers.Object(None)
    store = providers.ThreadSafeSingleton(make_store, __self__, settings)
    services = providers.ThreadSafeSingleton(Services, __self__)
    unit_of_work = providers.Factory(lambda store: store.unit_of_work(), store)
    bound_services = providers.Callable(bound_services, __self__)
    send = providers.Object(send_input)
    grant = providers.Object(send_grant)
    answer = providers.Object(send_answer)
    step = providers.Object(send_step)
    evidence = providers.ThreadSafeSingleton(FileEvidenceReader)
    repository_writer = providers.ThreadSafeSingleton(RepositoryWriter)
    _document_root = providers.Callable(lambda settings: settings['home'] / 'projects', settings)
    project_documents = providers.ThreadSafeSingleton(ProjectDocuments, _document_root)
    _attention_repository = providers.ThreadSafeSingleton(AttentionRepository, store, unit)
    workspace_repository = providers.ThreadSafeSingleton(WorkspaceRepository, store, unit)
    triage_repository = providers.ThreadSafeSingleton(TriageRepository, store, unit)
    _records_repository = providers.ThreadSafeSingleton(RecordsRepository, store, unit)
    _authority_repository = providers.ThreadSafeSingleton(AuthorityRepository, store, unit)
    _library_repository = providers.ThreadSafeSingleton(LibraryRepository, store, unit)
    _attention_bound = providers.Callable(bound_callback, services, 'attention')
    _work_bound = providers.Callable(bound_callback, services, 'work')
    _execution_bound = providers.Callable(bound_callback, services, 'execution')
    _records_bound = providers.Callable(bound_callback, services, 'records')
    _work_repository = providers.ThreadSafeSingleton(WorkRepository, store, _attention_bound, unit)
    _execution_repository = providers.ThreadSafeSingleton(ExecutionRepository, store, unit,
        attention=_attention_bound,
        decisions=providers.Callable(lambda services: lambda unit: decision_repository(services, unit), services),
        collaborators=providers.Callable(lambda services: lambda unit: collaborators(services, unit), services))
    _decisions_repository = providers.ThreadSafeSingleton(DecisionRepository, store,
        _attention_bound, _work_bound, _execution_bound, records=_records_bound, unit=unit)
    workspace = providers.ThreadSafeSingleton(WorkspaceFacade, workspace_repository)
    workspace_actor = providers.Factory(WorkspaceFacade, workspace_repository)
    records = providers.ThreadSafeSingleton(make_records, _records_repository, repository_writer,
        workspace, services, settings.provided['management_home'])
    attention = providers.ThreadSafeSingleton(AttentionFacade, _attention_repository, store.provided.clock,
        mandate=providers.Callable(lambda services: lambda project: services.records.triage_mandate(project), services),
        routing_history=services.provided.routing_history)
    work = providers.ThreadSafeSingleton(WorkFacade, _work_repository, evidence, store.provided.clock,
        records=records, authority=providers.Callable(callback, services, 'authority'))
    execution = providers.ThreadSafeSingleton(ExecutionFacade, _execution_repository, work,
        decision_source=providers.Callable(lambda services: lambda: services.decisions.list(), services),
        send=send, grant=grant, answer=answer, step=step,
        prepare_dispatch=providers.Callable(prepare_dispatch, services),
        authority=providers.Callable(callback, services, 'authority'), clock=store.provided.clock)
    decisions = providers.ThreadSafeSingleton(DecisionsFacade, _decisions_repository, store.provided.clock,
        execution, records=records, authority=providers.Callable(callback, services, 'authority'))
    authority = providers.ThreadSafeSingleton(AuthorityFacade, _authority_repository,
        records, work, decisions, attention, execution)
    library = providers.ThreadSafeSingleton(LibraryFacade, _library_repository, work)
    references = providers.Factory(open_references, services)
    context = providers.Factory(open_context, services)
    jobs = providers.Factory(open_jobs, services)
    dispatch = providers.Factory(open_dispatch, services)
    projects = providers.Factory(open_projects, services)
    configuration = providers.Factory(open_configuration, services)
    work_commands = providers.Factory(open_work_commands, services)
    triage_policy = providers.Factory(open_triage_policy, services)
    attention_commands = providers.Factory(open_attention_commands, services)
    decision_commands = providers.Factory(open_decision_commands, services)
    history = providers.Factory(open_history, services)
    hosts = providers.Factory(open_hosts, services)
    controller_commands = providers.Factory(ControllerCommands, services)
    triage_commands = providers.Factory(TriageCommands, services)
    triage_scheduler = providers.Factory(TriageScheduler, services, deliver=None,
        host=transport.provided.host_by_name)
    notifications = providers.Factory(Notifications)
    attention_items = providers.Factory(attention_items, attention=attention)
    building_state = providers.Factory(building_state, workspace=workspace)
    decision_log = providers.Factory(decision_log, work=work, decisions=decisions)
    subject_history = providers.Factory(subject_history, store=store)
    project_status = providers.Factory(project_status, work=work, attention=attention,
        execution=execution, library=library, decisions=decisions)
    work_detail = providers.Factory(project_work_detail, work=work, attention=attention,
        execution=execution, library=library, decisions=decisions)
    history_runs = providers.Factory(history_runs, execution=execution, work=work, workspace=workspace)
    kept_run_detail = providers.Factory(kept_run_detail, execution=execution, work=work,
        library=library, documents=project_documents)
    annotate_workspace = providers.Factory(annotate, workspace=workspace)
    documents = providers.Factory(ProjectDocuments, root=_document_root)
    document_keeper = providers.Factory(DocumentKeeper)
