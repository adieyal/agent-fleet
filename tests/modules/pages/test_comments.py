"""Comments are ordinary attention requests; replies use the existing Decision path."""
from dataclasses import replace
from uuid import uuid4

import pytest

from fleet.container import configured_container
from tests.pages_fixture import seed_page


@pytest.fixture
def comments():
    container = configured_container()
    ids = seed_page(container)
    fields = dict(revision=ids['revision'], comment_id=str(uuid4()), headline='Scope of suppliers',
                  body='Should historical suppliers be included?', reason='The user must decide scope.',
                  selector={'type': 'TextQuoteSelector', 'exact': 'A shared view'}, actor='reader')
    return container, ids, fields


def comment(container, ids, fields, **changes):
    return container.page_change(project=ids['project'], slug='supplier-migration', operation='comment',
                                 **{**fields, **changes})


def test_comment_attention_mapping_and_idempotency_after_handover(comments):
    container, ids, fields = comments
    result = comment(container, ids, fields)
    item = container.attention().get(result['id'])
    assert (item.kind, item.owner, item.source, item.project) == ('decision', 'user', 'page', ids['project'])
    assert item.source_reference == f'page:{ids["project"]}:supplier-migration:{fields["comment_id"]}'
    assert item.context_reference == f'fleet://projects/{ids["project"]}/pages/supplier-migration'
    assert item.run is None and item.stream_context is None
    assert item.page_annotation.body == fields['body']
    assert item.page_annotation.revision == ids['revision']
    before = container.store().latest_sequence()
    assert comment(container, ids, fields) == result
    assert container.store().latest_sequence() == before
    with pytest.raises(ValueError, match='payload changed'):
        comment(container, ids, fields, body='Changed payload')
    # A low-level handover tests preservation without invoking project triage or accepting work.
    container.attention().commands.hand_over(result['id'], 'agent', 'user', reason='Requested', expected='user')
    assert comment(container, ids, fields)['id'] == result['id']
    assert container.attention().get(result['id']).owner == 'agent'
    assert container.attention().get(result['id']).page_annotation == item.page_annotation


def test_page_and_cli_answers_and_followup_share_the_thread(comments):
    container, ids, fields = comments
    result = comment(container, ids, fields)
    container.page_change(project=ids['project'], slug='supplier-migration', operation='answer',
                          item_id=result['id'], answer='Only current suppliers', actor='user')
    thread = container.page_view(project=ids['project'], slug='supplier-migration')['threads'][0]
    assert thread['state'] == 'resolved'
    assert thread['answers'][0]['answer'] == 'Only current suppliers'
    followup = comment(container, ids, fields, comment_id=str(uuid4()), parent=result['id'], body='What about old contracts?')
    container.attention_commands().answer(followup['id'], 'Track them separately', actor='user', next_step=None)
    threads = container.page_view(project=ids['project'], slug='supplier-migration')['threads']
    assert len(threads) == 2
    assert threads[1]['annotation']['parent'] == result['id']
    assert threads[1]['answers'][0]['answer'] == 'Track them separately'
    assert container.execution().repository.deliveries() == []


def test_block_anchor_survives_directive_values_and_disappears_explicitly(comments):
    container, ids, fields = comments
    result = comment(container, ids, fields, selector={'type': 'FragmentSelector', 'value': 'supplier-work'})
    container.work().set(ids['work'], actor='user', next_step='New step')
    thread = container.page_view(project=ids['project'], slug='supplier-migration')['threads'][0]
    assert thread['attachment']['state'] == 'attached'
    container.records().write(ids['project'], 'pages/supplier-migration.md', '# Replaced\n', key='replacement', actor='author')
    thread = container.page_view(project=ids['project'], slug='supplier-migration')['threads'][0]
    assert thread['id'] == result['id']
    assert thread['attachment']['reason'] == 'Anchor unavailable: block supplier-work removed'


@pytest.mark.parametrize('changes,reason', [
    ({'selector': {'type': 'CssSelector', 'value': 'body'}}, 'Unknown selector'),
    ({'selector': {'type': 'TextQuoteSelector', 'exact': ''}}, 'quote exact'),
    ({'selector': {'type': 'TextQuoteSelector', 'exact': 'missing phrase'}}, 'quoted text changed'),
    ({'selector': {'type': 'FragmentSelector', 'value': 'unknown'}}, 'block unknown removed'),
    ({'selector': [{'type': 'TextQuoteSelector', 'exact': 'A shared view'},
                   {'type': 'TextPositionSelector', 'start': 0, 'end': 5}]}, 'positions disagree'),
    ({'revision': 'HEAD'}, 'Unknown confirmed page revision'),
    ({'owner': 'supervisor'}, 'owner must'),
    ({'reason': ''}, 'reason is required'),
    ({'body': 'x' * 8193}, 'exceeds 8 KiB'),
    ({'comment_id': 'invalid'}, 'canonical UUID'),
])
def test_invalid_comments_are_atomic(comments, changes, reason):
    container, ids, fields = comments
    before = container.store().latest_sequence()
    with pytest.raises((ValueError, LookupError), match=reason):
        comment(container, ids, fields, **changes)
    assert container.store().latest_sequence() == before
    assert not any(item.source == 'page' for item in container.attention().list())


def test_changed_and_ambiguous_quotes_remain_in_threads(comments):
    container, ids, fields = comments
    comment(container, ids, fields)
    container.records().write(ids['project'], 'pages/supplier-migration.md',
        'A shared view\n\nA shared view\n', key='duplicate-quote', actor='author')
    thread = container.page_view(project=ids['project'], slug='supplier-migration')['threads'][0]
    assert thread['attachment']['state'] == 'ambiguous'
    container.records().write(ids['project'], 'pages/supplier-migration.md', 'New prose', key='new-prose', actor='author')
    thread = container.page_view(project=ids['project'], slug='supplier-migration')['threads'][0]
    assert thread['attachment']['state'] == 'unavailable'
    assert thread['annotation']['body'] == fields['body']


def test_prose_canonical_text_includes_formatted_unicode_and_positions(comments):
    container, ids, fields = comments
    body = '# Formatted\n\nA **shared** view of 🍋 suppliers &amp; contracts.\n'
    record = container.records().write(ids['project'], 'pages/formatted.md', body, key='formatted', actor='author')
    view = container.page_view(project=ids['project'], slug='formatted')
    assert view['nodes'][0]['prose_text'] == 'Formatted A shared view of 🍋 suppliers & contracts.'
    exact = '🍋 suppliers'
    text = view['nodes'][0]['prose_text']
    start = text.index(exact)
    selector = [{'type': 'TextQuoteSelector', 'exact': exact},
                {'type': 'TextPositionSelector', 'start': start, 'end': start + len(exact)}]
    result = container.page_change(project=ids['project'], slug='formatted', operation='comment',
                                  **{**fields, 'revision': record['revision'], 'selector': selector})
    assert result['state'] == 'open'


def test_cross_page_parent_and_answers_are_refused(comments):
    container, ids, fields = comments
    parent = comment(container, ids, fields)
    other = container.records().write(ids['project'], 'pages/other.md', 'A shared view', key='other', actor='author')
    with pytest.raises(ValueError, match='another page'):
        container.page_change(project=ids['project'], slug='other', operation='comment',
            **{**fields, 'revision': other['revision'], 'comment_id': str(uuid4()), 'parent': parent['id']})
    with pytest.raises(ValueError, match='does not belong'):
        container.page_change(project=ids['project'], slug='other', operation='answer',
                              item_id=parent['id'], answer='Wrong page', actor='user')


def test_existing_attention_loads_without_annotation(comments):
    container, ids, fields = comments
    assert container.attention().get(ids['attention']).page_annotation is None
    result = comment(container, ids, fields, owner='agent', reason='Reader requests an agent response.')
    item = container.attention().get(result['id'])
    assert replace(item, state='acknowledged').page_annotation == item.page_annotation
    reopened = configured_container(path=container.store().path)
    assert reopened.attention().get(item.id).page_annotation == item.page_annotation


def test_repurposed_block_never_anchors_to_another_record(comments):
    container, ids, fields = comments
    comment(container, ids, fields, selector={'type': 'FragmentSelector', 'value': 'supplier-work'})
    other = container.work().add(project=ids['project'], title='Other scope', goal='Different record', actor='fixture')
    body = f'::work{{id={other.id} block=supplier-work}}'
    container.records().write(ids['project'], 'pages/supplier-migration.md', body, key='repurposed', actor='author')
    thread = container.page_view(project=ids['project'], slug='supplier-migration')['threads'][0]
    assert thread['attachment']['reason'] == 'Anchor unavailable: block supplier-work changed target'


def test_metadata_survives_acknowledge_snooze_resolution_and_reobservation(comments):
    from datetime import timedelta
    container, ids, fields = comments
    result = comment(container, ids, fields)
    original = container.attention().get(result['id'])
    facade = container.attention()
    facade.acknowledge(result['id'], actor='user')
    facade.snooze(result['id'], actor='user', until=container.store().clock() + timedelta(days=1))
    facade.raise_item(project=ids['project'], source='page', source_reference=original.source_reference,
                      kind='decision', owner='user', headline=original.headline,
                      context_reference=original.context_reference, actor='system')
    assert facade.get(result['id']).page_annotation == original.page_annotation
    container.attention_commands().answer(result['id'], 'Disposition', actor='user', next_step=None)
    assert facade.get(result['id']).page_annotation == original.page_annotation


def test_generic_attention_command_cannot_mutate_a_page_comment(comments):
    container, ids, fields = comments
    result = comment(container, ids, fields)
    original = container.attention().get(result['id'])
    with pytest.raises(ValueError, match='immutable'):
        container.attention().raise_item(project=ids['project'], kind='decision', owner='user',
            source='page', source_reference=original.source_reference, headline='Changed headline',
            context_reference=original.context_reference, actor='generic')
    assert container.attention().get(result['id']) == original


def test_unresolved_directive_cannot_claim_an_attached_block(comments):
    container, ids, fields = comments
    with pytest.raises(ValueError, match='Cannot anchor unresolved directive: Unknown work item missing-work'):
        comment(container, ids, fields, selector={'type': 'FragmentSelector', 'value': 'missing-record'})


def test_compact_comment_preserves_attention_payload_and_resolve_is_page_scoped(comments):
    container, ids, fields = comments
    compact = {key: fields[key] for key in ('revision', 'comment_id', 'body', 'selector', 'actor')}
    result = container.page_change(project=ids['project'], slug='supplier-migration',
                                   operation='comment_text', owner='agent', **compact)
    item = container.attention().get(result['id'])
    assert item.kind == 'decision' and item.owner == 'agent'
    assert item.page_annotation.body == fields['body']
    assert item.headline == fields['body']
    assert item.page_annotation.reason == f'agent must respond to this page comment: {fields["body"]}'
    assert container.page_change(project=ids['project'], slug='supplier-migration',
                                 operation='comment_text', owner='agent', **compact)['id'] == result['id']
    with pytest.raises(ValueError, match='does not belong to this page'):
        container.page_change(project=ids['project'], slug='another-page', operation='resolve',
                              item_id=result['id'], actor='user')
    assert container.attention().get(result['id']).state == 'open'
    container.page_change(project=ids['project'], slug='supplier-migration', operation='resolve',
                          item_id=result['id'], actor='user')
    thread = container.page_view(project=ids['project'], slug='supplier-migration')['threads'][0]
    assert thread['state'] == 'resolved' and thread['answers'] == []
    assert container.attention().get(result['id']).resolution_details == 'Resolved from the page margin without an answer.'
