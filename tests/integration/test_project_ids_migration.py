from fleet.composition import open_attention, open_execution, open_library, open_store, open_work, open_workspace
from fleet.infrastructure.sqlite.migrations.project_ids import migrate_project_ids


def test_migrate_unique_and_report_ambiguous():
    store = open_store()
    workspace = open_workspace(store)
    identity = workspace.edit_registry(lambda registry: registry.create('Unique')).id
    candidates = [workspace.edit_registry(lambda registry: registry.create('Repeated')).id for _ in range(2)]
    work, attention = open_work(store), open_attention(store)
    unique = work.add(project='Unique', title='One', goal='Ship', actor='user')
    ambiguous = work.add(project='Repeated', title='Two', goal='Ship', actor='user')
    unknown = work.add(project='Missing', title='Three', goal='Ship', actor='user')
    question = attention.raise_item(project='Unique', kind='decision', owner='user', source='manual',
        source_reference='q', headline='Choose', context_reference='doc', actor='user')
    library = open_library(store)
    library.link('https://example.org/report', project='Unique', actor='user')
    execution = open_execution(store)
    run = execution.dispatch(unique.id, host='worker', runtime='codex', payload={'cwd': '/repo'},
                             actor='user', reason='Ship', idempotency_key='request').run
    before = store.history_after(0)
    action = execution.get_action(run.action)
    report = migrate_project_ids(store, workspace)
    assert work.get(unique.id).project == identity
    assert work.get(ambiguous.id).project == 'Repeated'
    assert work.get(unknown.id).project == 'Missing'
    assert attention.get(question.id).project == identity
    assert library.list()[0].project == identity
    assert execution.get_action(run.action).project == identity
    assert execution.get_action(run.action).payload_fingerprint == action.payload_fingerprint
    assert execution.get_run(run.id) == run
    assert all(candidate in '\n'.join(report) for candidate in candidates)
    assert 'unchanged' in '\n'.join(report) and 'unknown' in '\n'.join(report)
    assert store.history_after(0)[:len(before)] == before
    sequence = store.latest_sequence()
    migrate_project_ids(store, workspace)
    assert store.latest_sequence() == sequence
