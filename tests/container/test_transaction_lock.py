"""Ingest scope construction must leave SQLite available to other writers."""
from contextlib import closing
import sqlite3
import time

from fleet import container as composition
from fleet.ingestion import observe_runs
from fleet.infrastructure.sqlite.store import UnitOfWork


def test_ingest_builds_scopes_before_acquiring_write_lock(monkeypatch):
    container = composition.Container()
    container.initialized_workspace()
    services = container.services()
    services.execution
    services.library
    make_scope = composition.Container
    scope_count = 0

    def checked_scope():
        nonlocal scope_count
        scope_count += 1
        with closing(sqlite3.connect(services.store.path, timeout=0)) as competitor:
            competitor.execute('BEGIN IMMEDIATE')
            competitor.rollback()
        return make_scope()

    monkeypatch.setattr(composition, 'Container', checked_scope)
    host = dict(name='recorded', ok=True, jobs={str(i): dict(
        id=str(i), status='done', agent='codex', steps=[], project='p') for i in range(12)})
    observe_runs(services.execution, services.library, host)
    assert scope_count == 36
    assert len(services.execution.runs()) == 12


def test_ingest_slow_scope_preparation_is_outside_transaction(monkeypatch):
    container = composition.Container()
    container.initialized_workspace()
    services = container.services()
    services.execution
    services.library
    make_scope = composition.Container
    holds = []
    enter, exit_unit = UnitOfWork.__enter__, UnitOfWork.__exit__

    def slow_scope():
        time.sleep(1.1)
        return make_scope()

    def timed_enter(unit):
        result = enter(unit)
        unit.started = time.monotonic()
        return result

    def timed_exit(unit, *args):
        try:
            return exit_unit(unit, *args)
        finally:
            holds.append(time.monotonic() - unit.started)

    monkeypatch.setattr(composition, 'Container', slow_scope)
    monkeypatch.setattr(UnitOfWork, '__enter__', timed_enter)
    monkeypatch.setattr(UnitOfWork, '__exit__', timed_exit)
    observe_runs(services.execution, services.library, dict(name='recorded', ok=True,
        jobs={'one': dict(id='one', status='done', steps=[], project='p')}))
    assert max(holds) < 1.0, holds


def test_live_catch_up_transport_leaves_store_unlocked(monkeypatch):
    from fleet import transport
    from fleet.services.live import apply_message

    container = composition.Container()
    host = transport.Host('recorded', None)
    state = container.live_state(hosts=[host])
    calls = []

    def catch_up(*args):
        with closing(sqlite3.connect(state.store.path, timeout=0)) as competitor:
            competitor.execute('BEGIN IMMEDIATE')
            competitor.rollback()
        calls.append(args)
        return []

    monkeypatch.setattr(transport, 'catch_up_jobs', catch_up)
    monkeypatch.setattr(transport, 'catch_up_sessions', catch_up)
    apply_message(state, host, {'type': 'hello'})
    assert len(calls) == 2


def test_triage_prepares_scope_without_write_lock(monkeypatch, project_id):
    container = composition.configured_container()
    services = container.services()
    make_scope = composition.Container
    prepared = []

    def checked_scope():
        with closing(sqlite3.connect(services.store.path, timeout=0)) as competitor:
            competitor.execute('BEGIN IMMEDIATE')
            competitor.rollback()
        prepared.append(True)
        return make_scope()

    monkeypatch.setattr(composition, 'Container', checked_scope)
    assert container.triage_scheduler(deliver=None, host=None).reserve(project_id) is None
    assert prepared
