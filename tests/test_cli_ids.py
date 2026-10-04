import json

import pytest

from fleet.container import configured_container
from fleet.cli import main


def test_prefix_resolution_rejects_ambiguity_and_prefers_exact():
    from fleet.cli import FleetError, resolve_cli_id
    assert resolve_cli_id('abc', ['abc', 'abcdef'], 'work item') == 'abc'
    assert resolve_cli_id('abcd', ['abcdef'], 'work item') == 'abcdef'
    with pytest.raises(FleetError, match='ambiguous.*abcdef.*abcxyz'):
        resolve_cli_id('abc', ['abcdef', 'abcxyz'], 'work item')


def test_status_ids_can_be_used_for_work_criterion_and_attention(project_id, capsys):
    work = configured_container().work()
    item = work.add(project=project_id, title='Ship', goal='Release', actor='test')
    criterion = work.add_criterion(item.id, text='Reviewed', verification='judged', actor='test')
    attention = configured_container().initialized_attention().raise_item(project=project_id, kind='decision', owner='user', source='manual', source_reference='choice', headline='Choose', context_reference='doc', work_item=item.id, actor='test')
    main(['status', project_id])
    output = capsys.readouterr().out
    for identity in (item.id, criterion.id, attention.id):
        assert identity[:8] in output
        assert identity not in output
    main(['work', 'set', item.id[:8], '--title', 'Shipped', '--actor', 'test'])
    assert json.loads(capsys.readouterr().out)['id'] == item.id
    main(['criterion', 'meet', criterion.id[:8], '--actor', 'test'])
    assert json.loads(capsys.readouterr().out)['id'] == criterion.id
    main(['attention', 'ack', attention.id[:8], '--actor', 'test'])
    assert json.loads(capsys.readouterr().out)['id'] == attention.id


def test_missing_work_item_names_status(capsys):
    with pytest.raises(SystemExit):
        main(['work', 'set', 'missing', '--title', 'Title', '--actor', 'test'])
    assert 'fleet status PROJECT' in capsys.readouterr().err


def test_work_references_and_answer_expand_before_writing(project_id, capsys):
    work = configured_container().work()
    parent = work.add(project=project_id, title='Parent', goal='Ship', actor='test')
    main(['work', 'add', 'Child', '--project', project_id, '--goal', 'Test',
          '--parent', parent.id[:8], '--actor', 'test'])
    child = json.loads(capsys.readouterr().out)
    assert child['parent'] == parent.id
    main(['work', 'relate', child['id'][:8], parent.id[:8], '--actor', 'test'])
    capsys.readouterr()
    attention = configured_container().initialized_attention().raise_item(project=project_id, kind='decision', owner='user', source='manual', source_reference='answer', headline='Choose', context_reference='doc', work_item=child['id'], actor='test')
    main(['answer', attention.id[:8], 'Proceed', '--actor', 'test'])
    assert json.loads(capsys.readouterr().out)['attention_item'] == attention.id
