"""Compatibility entrypoints backed by the library container."""
from fleet import container as _container
from fleet.container import Services as Facades, facades, management_home, store_path
from fleet.container import Store, ProjectDocuments, fleet_home, send_input, send_grant, send_answer, send_step

# Retained public ingestion helpers during the adapter migration.
from fleet.ingestion import observe_runs, observe_sessions, record_decisions
from fleet.services.documents import DocumentKeeper, STATUS_LINE, is_private


def open_store(path=None, *, clock=None):
    container = _container.Container()
    settings = dict(container.settings())
    if path is not None:
        settings['store_path'] = path
    if clock is not None:
        settings['clock'] = clock
    container.settings.override(settings)
    return container.store()


def open_attention(store=None, *, workspace_path=None):
    return _container.open_attention(store, workspace_path=workspace_path)


def open_work(store=None):
    return _container.open_work(store)


def open_records(store=None):
    return _container.open_records(store)


def open_workspace(store=None, *, initial=None, actor='user'):
    return _container.open_workspace(store, initial=initial, actor=actor)


def open_execution(store=None):
    return _container.open_execution(store)


def storage_usage(store=None):
    return _container.storage_usage(store)


def open_library(store=None):
    return _container.open_library(store)


def open_decisions(store=None):
    return _container.open_decisions(store)


def open_authority(store=None):
    return _container.open_authority(store)


def deliver_triage(services, run, *, reconcile=False):
    return _container.deliver_triage(services, run, reconcile=reconcile)


def open_documents(root=None):
    container = _container.Container()
    return container.documents() if root is None else container.documents(root=root)


def open_document_keeper(documents, fetch, *, keep_trace=None):
    return _container.Container().document_keeper(documents, fetch, keep_trace=keep_trace)


def open_references(store=None):
    return facades(store).container.references()


def open_context(store=None):
    return facades(store).container.context()


def open_jobs(store=None):
    return facades(store).container.jobs()


def open_hosts():
    return _container.Container().hosts()


def open_controller_commands(store, activation):
    return facades(store).container.controller_commands(activation)


def open_dispatch(store=None):
    return facades(store).container.dispatch()


def open_projects(store=None):
    return facades(store).container.projects()


def open_configuration(store=None):
    return facades(store).container.configuration()


def open_work_commands(store=None):
    return facades(store).container.work_commands()


def open_triage_policy(store=None):
    return facades(store).container.triage_policy()


def open_attention_commands(store=None):
    return facades(store).container.attention_commands()


def open_decision_commands(store=None):
    return facades(store).container.decision_commands()


def open_history(store=None):
    return facades(store).container.history()


def stored_run_detail(identity, store, documents=None):
    return _container.stored_run_detail(identity, store, documents)


def work_detail(reference, store=None):
    return _container.work_detail(reference, store)
