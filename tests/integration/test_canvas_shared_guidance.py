"""Canvas guidance and dispatch use the same Records document."""
import json

from test_canvas_kernel import space, clock, ok, op, state, task


def test_canvas_edit_versions_constitution_and_dispatch_context(space, tmp_path):
    records = space.container.records()
    records.write_guidance(space.project, '# Constitution\n\nKeep the API stable.\n', actor='user')
    result = ok(space, 'charter.update', patch={'north_star': 'Ship the shared API',
                                             'scope': {'impl': 'tell'}})
    guidance = records.guidance(space.project)
    assert 'Keep the API stable.' in guidance.body
    assert 'Ship the shared API' in guidance.body
    assert result['version'] == guidance.version.number
    assert state(space)['charter']['scope']['impl'] == 'tell'
    identity = task(space)
    pinned = records.dispatch_guidance(identity)
    records.write_guidance_files(pinned, tmp_path)
    assert (tmp_path / 'CONSTITUTION.md').read_text() == guidance.body
    raw = space.canvas.reader().repository.load(space.project)
    assert 'charter' not in raw


def test_existing_constitution_is_preserved_when_canvas_initializes(clock):
    from fleet.container import configured_container
    container = configured_container(clock=clock)
    project = container.initialized_workspace().edit_registry(lambda r: r.create('guided')).id
    records = container.records()
    records.write_guidance(project, '# Constitution\n\nPreserve this rule.\n', actor='user')
    container.canvas().init(project, actor='user', north_star='New north star')
    assert 'Preserve this rule.' in records.guidance(project).body
    assert 'New north star' in records.guidance(project).body


def test_edit_from_another_client_updates_reader_and_rejects_stale_canvas(space):
    records = space.container.records()
    before = state(space)['charter']
    records.write_space_guidance(space.project, before | {'north_star': 'Edited from CLI',
                                'scope': {'impl': 'ask'}}, actor='cli-user', base=before['version'])
    assert state(space)['charter']['north_star'] == 'Edited from CLI'
    assert ok(space, 'decision.check', kind='impl')['level'] == 'ask'
    refused = op(space, 'charter.update', patch={'north_star': 'Stale'}, base=before['version'])
    assert refused['code'] == 'version_conflict'
    assert records.space_guidance(space.project)['north_star'] == 'Edited from CLI'


def test_migration_preserves_legacy_history_without_overwriting_constitution(clock):
    from fleet.container import configured_container
    container = configured_container(clock=clock)
    project = container.initialized_workspace().edit_registry(lambda r: r.create('legacy')).id
    canvas = container.canvas()
    canvas.init(project, actor='user')
    records = container.records()
    records.write_guidance(project, '# Constitution\n\nExisting prose.\n', actor='user')
    first = {'id': 'main', 'version': 1, 'north_star': 'Original outcome',
             'clauses': [], 'scope': {'impl': 'tell'}, 'written_by': 'legacy-user'}
    second = first | {'version': 2, 'north_star': 'Current outcome'}
    with canvas.scope() as (_, facade):
        connection = facade.repository.require_unit().connection
        connection.execute('INSERT INTO canvas_record (space, kind, id, record) VALUES (?, ?, ?, ?)',
                           (project, 'charter', 'main', json.dumps(second)))
        for value in (first, second):
            connection.execute('INSERT INTO canvas_version (space, kind, id, version, record) VALUES (?, ?, ?, ?, ?)',
                               (project, 'charter', 'main', value['version'], json.dumps(value)))
        facade.repository.require_unit().record_change('legacy-charter-fixture', '', json.dumps(second), 'test')
    model = canvas.state(project, person='user')
    assert model['charter']['north_star'] == 'Current outcome'
    assert model['charter']['scope'] == {'impl': 'tell'}
    history = canvas.versions(project, 'charter', 'main')
    assert any('Original outcome' in entry['body'] for entry in history)
    assert 'Existing prose.' in records.guidance(project).body
    number = records.guidance(project).version.number
    canvas.state(project, person='user')
    assert records.guidance(project).version.number == number
    assert canvas.reader().repository.load(project)['charter']['main'] == second


def test_missing_scope_is_visible_instead_of_defaulting_to_ask(space):
    space.container.records().write_guidance(space.project, '# Constitution\n\nNo scope recorded.\n', actor='user')
    assert state(space)['charter']['scope'] == {}
    result = op(space, 'decision.check', kind='impl')
    assert result['code'] == 'not_found'
    assert 'no decision scope for impl' in result['message']
    before = space.container.records().guidance(space.project)
    assert ok(space, 'charter.update', patch={'scope': {}})['version'] == before.version.number
    assert space.container.records().guidance(space.project).body == before.body


def test_canvas_brief_reads_full_constitution_and_epic_charter(space):
    epic = ok(space, 'epic.create', title='Shared epic')['epic']
    records = space.container.records()
    current = records.guidance(space.project)
    records.write_guidance(space.project, current.body + '\nUse the stable API.\n', actor='user')
    records.write_guidance(space.project, '# Epic charter\n\nKeep backward compatibility.\n', epic=epic, actor='user')
    identity = task(space, 'Build', epic=epic, stage='first')
    with space.canvas.scope() as (facades, facade):
        engine = facade.engine(space.project, space.canvas.ports(facades, space.project), actor='test')
        run = engine.put('run', 'brief-test', {'id': 'brief-test', 'item': identity, 'role': 'builder'})
        brief = engine.brief(run['id'])
    assert 'Use the stable API.' in brief
    assert 'Keep backward compatibility.' in brief
