"""Seed only temporary Fleet stores and management repositories."""
from pathlib import Path

from fleet.modules.execution import JobObservation


def seed_page(container):
    project = container.initialized_workspace().edit_registry(lambda registry: registry.create('supplier-demo')).id
    work = container.work().add(project=project, kind='epic', title='Active supplier slice',
        goal='Move current suppliers to the new catalog with verified contracts.',
        next_step='Verify the contract mapping, then accept the migration evidence.', actor='fixture')
    container.work().add(project=project, kind='milestone', parent=work.id,
        title='Verify supplier contracts', goal='Check the mapping', actor='fixture')
    attention = container.attention().raise_item(project=project, kind='decision', owner='user',
        source='fixture', source_reference='supplier-question', headline='Include historical suppliers in this slice?',
        context_reference='Current suppliers are ready; historical contracts need a scope decision.',
        actor='fixture', owner_reason='The user must decide whether to extend the agreed scope.')
    container.execution().record_host('home', reachable=True, error=None)
    container.execution().record_observed('home', dict(id='supplier-check', status='done',
        start=container.store().clock().timestamp(), end=container.store().clock().timestamp(),
        description='Supplier contract verification', runtime='codex'), project=project)
    now = container.store().clock()
    container.execution().observe('home', JobObservation('supplier-check', 'done', 'codex', now, now, now))
    source = (Path(__file__).parent / 'fixtures/pages/supplier-migration.md').read_text()
    body = source.replace('WORK', work.id).replace('ATTENTION', attention.id).replace('PROJECT', project)
    record = container.records().write(project, 'pages/supplier-migration.md', body,
        key='demo-page', actor='fixture', source_run=None)
    assert record['state'] == 'confirmed', record
    return dict(project=project, work=work.id, attention=attention.id, revision=record['revision'])
