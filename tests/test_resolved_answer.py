"""Resolved answers explain their history without changing it."""
from dataclasses import replace
from datetime import datetime, timezone
import shlex

import pytest

from fleet import cli, composition
from fleet.modules.attention import ItemResolved


@pytest.fixture
def resolved(project_id):
    work = composition.open_work().add(project=project_id, title='Build', goal='Ship', actor='user')
    attention = composition.open_attention()
    item = attention.raise_item(project=project_id, work_item=work.id, kind='decision', owner='user',
        source='manual', source_reference='resolved-answer', headline='Build in which order?',
        context_reference='brief', actor='author')
    attention.commands.clock = lambda: datetime(2026, 10, 4, 15, 2, tzinfo=timezone.utc)
    item = attention.resolve(item.id, details='Build in this order: API, then deck.', actor='resolver')
    return attention, item


@pytest.mark.parametrize('missing', [False, True])
def test_resolution_message(resolved, missing):
    attention, item = resolved
    if missing:
        item = replace(item, resolution_details=None)
        with attention.repository.transaction() as repository:
            repository.write(item)
            repository.unit.record_change("test:legacy-details", "present", "missing", "test")
    with attention.repository.transaction() as repository:
        repository.save(item, item.state, "host-stream")
    before = composition.open_store().latest_sequence()
    with pytest.raises(ItemResolved) as caught:
        composition.open_decisions().answer(item.id, "Use 'deck' first", actor='user')
    details = 'details not recorded' if missing else 'Build in this order: API, then deck.'
    assert str(caught.value).splitlines()[0] == (
        f'attention item {item.id[:8]} was resolved by resolver at 2026-10-04 15:02 UTC: {details}')
    command = shlex.split(str(caught.value).splitlines()[-1])
    assert command[:3] == ['fleet', 'decision', 'record']
    assert command[command.index('--work-item') + 1] == item.work_item
    assert command[command.index('--answer') + 1] == "Use 'deck' first"
    assert composition.open_store().latest_sequence() == before


def test_missing_actor_and_time_are_explicit(resolved):
    attention, item = resolved
    with attention.repository.transaction() as repository:
        repository.unit.connection.execute('DELETE FROM state_history WHERE subject = ?', (f'attention:{item.id}',))
        repository.unit.record_change('test:history-pruned', '', 'pruned', 'test')
    error = attention.resolved_answer_error(replace(item, resolved_at=None, work_item=None), 'Yes', 'user')
    assert 'resolved by actor not recorded at time not recorded' in str(error)
    assert 'work item not recorded' in str(error)
    assert 'fleet decision record' not in str(error)


def test_cli_resolved_answer_exit_and_text(resolved, capsys):
    attention, item = resolved
    with pytest.raises(SystemExit) as caught:
        cli.main(['answer', item.id, 'Deck first'])
    assert caught.value.code == 2
    output = capsys.readouterr()
    text = output.out + output.err
    assert 'was resolved by resolver at 2026-10-04 15:02 UTC' in text
    assert 'Build in this order: API, then deck.' in text
    assert 'fleet decision record' in text
    assert item.work_item in text
    command = shlex.split(text.strip().splitlines()[-1])
    assert command[command.index('--answer') + 1] == 'Deck first'
    cli.build_parser().parse_args(command[1:])
