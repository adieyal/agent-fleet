"""Merge preserves project-owned records and the immutable decisions/runs they link."""
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from fleet.composition import facades, open_store, open_workspace
from fleet.modules.work import WorkItem
from fleet.modules.execution import Action, Run
from fleet.modules.decisions import Decision
from fleet.infrastructure.sqlite.execution import encode_run
from fleet.projections.decisions import decision_log


def seeded():
    workspace = open_workspace()
    keep = workspace.move_in(['home'], 'keep').project_id
    other = workspace.move_in(['home'], 'other').project_id
    store = open_store()
    now = datetime.now(timezone.utc)
    item = WorkItem('work', other, None, 'task', 'Preserve work', 'Keep audit evidence',
                    'blocked', None, 'Review merge', None, now, now)
    action = Action('action', 'work', 'test', project=other)
    decision = Decision('decision', 'attention', 'Keep work?', 'Yes', 'test', '', ('work',), now)
    run = Run('run', 'action', 'home', 'job', None, 'failed', None, now, now, now)
    encode = lambda record: json.dumps(asdict(record), default=lambda value: value.isoformat())
    with store.unit_of_work() as unit:
        for table, identity, record in [
            ('work_item', 'work', encode(item)),
            ('execution_action', 'action', encode(action)),
            ('decisions_decision', 'decision', encode(decision)),
        ]:
            unit.connection.execute(f'INSERT INTO {table} (id, record) VALUES (?, ?)', (identity, record))
        record, observation = encode_run(run)
        unit.connection.execute('INSERT INTO execution_run VALUES (?, ?, ?, ?, ?)',
                                ('run', 'action', 'home', 'job', json.dumps(record)))
        unit.connection.execute('INSERT INTO execution_run_observation (run, record) VALUES (?, ?)',
                                ('run', json.dumps(observation)))
        unit.connection.execute("""INSERT INTO attention_item
            (id,project,kind,owner,source,source_reference,headline,context_reference,state,last_seen)
            VALUES ('attention',?,'blocker','job:home:job','test','ref','Blocked','','open','now')""", (other,))
        unit.record_change("seed", "", "records", "test")
    return workspace, store, keep, other


def test_merge_rehomes_work_attention_runs_and_decisions():
    workspace, store, keep, other = seeded()
    services = facades(store)
    original_run = services.execution.repository.get_run('run')
    original_decision = services.decisions.repository.get('decision')
    assert [record['id'] for record in decision_log(services.work, services.decisions, project=other)] == ['decision']
    result = workspace.merge(keep, other)
    with store.unit_of_work() as unit:
        for table in ('work_item', 'execution_action'):
            assert json.loads(unit.connection.execute(f'SELECT record FROM {table}').fetchone()[0])['project'] == keep
        assert unit.connection.execute('SELECT project FROM attention_item').fetchone()[0] == keep
    assert services.execution.repository.get_run('run') == original_run
    assert services.decisions.repository.get('decision') == original_decision
    assert [record['id'] for record in decision_log(services.work, services.decisions, project=keep)] == ['decision']
    assert decision_log(services.work, services.decisions, project=other) == []
    assert services.execution.repository.get_action(original_run.action).project == keep
    assert result.counts == {'work_items': 1, 'attention': 1, 'runs': 1, 'decisions': 1}
    assert other not in workspace.registry().projects


def test_record_failure_rolls_back_identity_and_all_records():
    workspace, store, keep, other = seeded()
    before = workspace.snapshot()
    with store.unit_of_work() as unit:
        unit.connection.execute("CREATE TRIGGER refuse_move BEFORE UPDATE ON attention_item BEGIN SELECT RAISE(ABORT, 'move refused'); END")
    with pytest.raises(sqlite3.IntegrityError, match='move refused'):
        workspace.merge(keep, other)
    assert workspace.snapshot() == before
    with store.unit_of_work() as unit:
        assert json.loads(unit.connection.execute('SELECT record FROM work_item').fetchone()[0])['project'] == other
