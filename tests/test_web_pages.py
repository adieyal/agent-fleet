"""Page routes over confirmed management records, including a real browser view."""
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from fleet.container import configured_container
from tests.pages_fixture import seed_page


@pytest.fixture
def demo_page(base_url, deck_state, override_web_store):
    container = configured_container()
    override_web_store(deck_state, container.store())
    ids = seed_page(container)
    return dict(**ids, base=base_url, url=f'{base_url}/pages/{ids["project"]}/supplier-migration')


def test_page_and_project_index_routes(demo_page):
    with urlopen(demo_page['url'], timeout=5) as response:
        html = response.read().decode()
        assert response.headers['Content-Type'] == 'text/html; charset=utf-8'
        assert "default-src 'none'" in response.headers['Content-Security-Policy']
    assert 'Supplier migration' in html
    assert 'Unknown work item missing-work' in html
    assert 'supplier-work' in html and 'Supplier contract verification' in html
    with urlopen(f'{demo_page["base"]}/pages/{demo_page["project"]}', timeout=5) as response:
        assert '/supplier-migration' in response.read().decode()
    with urlopen(f'{demo_page["base"]}/api/pages/{demo_page["project"]}/supplier-migration', timeout=5) as response:
        view = json.load(response)
        assert view['revision'] == demo_page['revision']
        assert next(node for node in view['nodes'] if node['kind'] == 'work')['record']['id'] == demo_page['work']
    with urlopen(f'{demo_page["base"]}/api/pages?project={demo_page["project"]}', timeout=5) as response:
        assert len(json.load(response)['pages']) == 1


@pytest.mark.parametrize('suffix,code,reason', [
    ('/absent', 404, 'Page not found'),
    ('/supplier-migration?revision=HEAD', 404, 'Unknown confirmed page revision'),
    ('/%2e%2e', 400, 'Invalid page slug'),
])
def test_missing_and_invalid_pages_name_the_error(demo_page, suffix, code, reason):
    with pytest.raises(HTTPError) as error:
        urlopen(f'{demo_page["base"]}/pages/{demo_page["project"]}{suffix}', timeout=5)
    assert error.value.code == code
    assert reason in json.load(error.value)['error']


def test_page_does_not_execute_authored_html(demo_page):
    container = configured_container()
    container.records().write(demo_page['project'], 'pages/hostile.md',
        '# Escaping\n\n<script>alert(1)</script>\n\n::work{id=<img/onerror=alert(1)>}',
        key='hostile', actor='fixture')
    with urlopen(f'{demo_page["base"]}/pages/{demo_page["project"]}/hostile', timeout=5) as response:
        html = response.read().decode()
    assert '<script>' not in html and '<img/onerror' not in html
    assert '&lt;script&gt;' in html


@pytest.mark.browser
def test_demo_page_in_browser(page, demo_page):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_viewport_size(dict(width=1280, height=1300))
    page.goto(demo_page['url'])
    assert page.get_by_role('heading', name='Supplier migration', exact=True).is_visible()
    assert page.locator('#supplier-work').inner_text().find('0 / 1 milestones') >= 0
    assert page.locator('#supplier-runs').inner_text().find('succeeded') >= 0
    assert page.get_by_role('alert').inner_text().startswith('Unknown work item missing-work')
    destination = os.environ.get('FLEET_PAGE_SCREENSHOT_DIR')
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path / 'M1-demo-desktop.png'), full_page=True)
    page.set_viewport_size(dict(width=390, height=844))
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    if destination:
        page.screenshot(path=str(Path(destination) / 'M1-demo-mobile.png'), full_page=True)
    page.get_by_role('link', name='Project pages', exact=True).click()
    assert page.get_by_role('link', name='Supplier migration', exact=True).is_visible()
    assert errors == []


def page_post(demo, operation, body, origin=True):
    from urllib.request import Request
    headers = {'Content-Type': 'application/json'}
    if origin:
        headers['Origin'] = demo['base'] if origin is True else origin
    request = Request(f'{demo["base"]}/api/pages/{demo["project"]}/supplier-migration/{operation}',
                      data=json.dumps(body).encode(), headers=headers, method='POST')
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def test_page_comment_http_contract_and_exact_origin(demo_page):
    from uuid import uuid4
    fields = dict(revision=demo_page['revision'], comment_id=str(uuid4()), headline='Scope?', body='Include history?',
        reason='User must decide scope.', selector={'type': 'FragmentSelector', 'value': 'supplier-work'})
    for origin in (False, 'https://evil.example', demo_page['base'].replace('http:', 'https:')):
        with pytest.raises(HTTPError) as error:
            page_post(demo_page, 'comments', fields, origin=origin)
        assert error.value.code == 403
    result = page_post(demo_page, 'comments', fields)
    assert result['state'] == 'open'
    assert page_post(demo_page, 'comments', fields)['id'] == result['id']
    answer = page_post(demo_page, 'answer', dict(item_id=result['id'], answer='Current suppliers only'))
    assert answer['attention_item'] == result['id']
    with urlopen(f'{demo_page["base"]}/api/pages/{demo_page["project"]}/supplier-migration', timeout=5) as response:
        thread = json.load(response)['threads'][0]
    assert thread['answers'][0]['answer'] == 'Current suppliers only'


def select_prose(page, phrase):
    """Perform an actual mouse selection over the displayed phrase."""
    position = page.evaluate('''phrase => {
      const el = document.querySelector('.page-prose p');
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let node;
      while (node = walker.nextNode()) {
        const start = node.textContent.indexOf(phrase);
        if (start >= 0) {
          const range = document.createRange(); range.setStart(node, start); range.setEnd(node, start + phrase.length);
          const rect = range.getBoundingClientRect();
          return {x: rect.x, y: rect.y + rect.height / 2, end: rect.right};
        }
      }
      throw new Error('phrase not found');
    }''', phrase)
    page.mouse.move(position['x'] + 1, position['y'])
    page.mouse.down()
    page.mouse.move(position['end'] - 1, position['y'], steps=12)
    page.mouse.up()


def fill_comment(page, headline, body):
    form = page.locator('#comment-form')
    form.get_by_label('Comment', exact=True).fill(body)
    destination = os.environ.get('FLEET_PAGE_SCREENSHOT_DIR')
    if destination and headline == 'History scope':
        page.locator('#comment-composer').screenshot(path=str(Path(destination) / 'M2-comment-composer.png'))
    form.get_by_role('button', name='Comment', exact=True).click()


@pytest.mark.browser
def test_select_comment_answer_and_cli_reply_in_browser(page, demo_page):
    from playwright.sync_api import expect
    from fleet_cli import cli

    errors, remote_requests = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(demo_page['base'])
               else (remote_requests.append(route.request.url), route.abort())[1])
    page.set_viewport_size(dict(width=1280, height=1100))
    page.goto(demo_page['url'])
    page.wait_for_function('typeof RecogitoJS !== "undefined"')
    assert errors == [], errors
    select_prose(page, 'A shared view')
    expect(page.get_by_role('button', name='Comment on selection')).to_be_visible()
    page.get_by_role('button', name='Comment on selection').click()
    expect(page.locator('#comment-composer')).to_be_visible()
    expect(page.locator('#comment-anchor')).to_contain_text('A shared view')
    fill_comment(page, 'History scope', 'Should this view include historical suppliers?')
    thread = page.locator('.thread').filter(has_text='Should this view include historical suppliers?')
    expect(thread).to_be_visible()
    expect(page.locator('.r6o-annotation')).to_be_visible()
    thread.get_by_label('Reply', exact=True).fill('Current suppliers only.')
    thread.get_by_role('button', name='Reply', exact=True).click()
    expect(thread.locator('.answer')).to_contain_text('Current suppliers only.')
    page.reload()
    expect(thread).not_to_have_class(re.compile(r'\bresolved\b'))
    expect(page.locator('.thread .answer')).to_contain_text('Current suppliers only.')
    page.locator('#supplier-work').get_by_role('button', name='Comment on block', exact=True).click()
    fill_comment(page, 'Contract evidence', 'Please verify the mapping evidence.')
    block = page.locator('.thread').filter(has_text='Please verify the mapping evidence.')
    expect(block).to_be_visible()
    item_id = block.get_attribute('data-attention-id')
    cli.main(['answer', item_id, 'Verified by the CLI.'], container=configured_container())
    expect(block.get_by_role('button', name='Resolved · Show')).to_be_visible(timeout=10000)
    block.get_by_role('button', name='Resolved · Show').click()
    expect(block.locator('.answer')).to_contain_text('Verified by the CLI.', timeout=10000)
    expect(block).to_have_class(re.compile(r'(?=.*\bresolved\b)(?=.*\bexpanded\b)(?=.*\bactive\b)'))
    destination = os.environ.get('FLEET_PAGE_SCREENSHOT_DIR')
    if destination:
        page.screenshot(path=str(Path(destination) / 'M2-comments-desktop.png'), full_page=True)
        page.locator('[aria-label="Page threads"]').screenshot(path=str(Path(destination) / 'M2-threads.png'))
        page.locator('.page-prose').first.screenshot(path=str(Path(destination) / 'M2-prose-anchor.png'))
        page.set_viewport_size(dict(width=390, height=844))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(Path(destination) / 'M2-comments-mobile.png'), full_page=True)
    assert errors == []
    assert remote_requests == []
    page.goto('about:blank')


@pytest.mark.browser
def test_detached_thread_and_followup_are_visible(page, demo_page):
    from uuid import uuid4
    from playwright.sync_api import expect
    fields = dict(revision=demo_page['revision'], comment_id=str(uuid4()), headline='Original quote',
        body='This comment must survive editing.', reason='User must decide.',
        selector={'type': 'TextQuoteSelector', 'exact': 'A shared view'})
    result = page_post(demo_page, 'comments', fields)
    container = configured_container()
    container.records().write(demo_page['project'], 'pages/supplier-migration.md',
        '# Updated page\n\nThe selected passage was removed.\n', key='remove-quote', actor='fixture')
    page.goto(demo_page['url'])
    thread = page.locator(f'#thread-{result["id"]}')
    expect(thread).to_contain_text('Anchor unavailable: quoted text changed')
    expect(thread.get_by_role('link', name='Open creation revision')).to_be_visible()
    thread.get_by_label('Reply').fill('Keep the old context visible.')
    thread.get_by_role('button', name='Reply').click()
    expect(thread.locator('.answer')).to_contain_text('Keep the old context visible.')
    thread.get_by_label('Reply').fill('Follow-up after edit')
    thread.get_by_role('button', name='Reply', exact=True).click()
    expect(thread.locator('.answer')).to_have_count(2)
    expect(thread).not_to_have_class(re.compile(r'\bresolved\b'))
    page_post(demo_page, 'comment-text', dict(revision=demo_page['revision'], comment_id=str(uuid4()),
        selector=fields['selector'], parent=result['id'], body='Linked follow-up after edit'))
    followup = page.locator('.thread').filter(has_text='Linked follow-up after edit')
    expect(followup).to_be_visible()
    expect(followup.get_by_role('link', name='Parent comment')).to_have_attribute('href', f'#thread-{result["id"]}')
    destination = os.environ.get('FLEET_PAGE_SCREENSHOT_DIR')
    if destination:
        page.screenshot(path=str(Path(destination) / 'M2-detached-thread.png'), full_page=True)
    page.set_viewport_size(dict(width=390, height=844))
    page.get_by_role('button', name='Close comments').click()
    page.get_by_role('button', name='Detached (2)', exact=True).click()
    expect(thread).to_be_visible()
    expect(followup).to_be_visible()
    page.goto('about:blank')


@pytest.mark.browser
def test_sse_updates_directive_and_library_answer_without_reload(page, demo_page, deck_state, monkeypatch):
    from threading import Event, Thread
    from uuid import uuid4
    from playwright.sync_api import expect
    from fleet.services.live import FleetState

    container = configured_container()
    comment = container.page_change(project=demo_page['project'], slug='supplier-migration', operation='comment',
        revision=demo_page['revision'], comment_id=str(uuid4()), headline='Live evidence',
        body='Can we verify this while the page stays open?', reason='User must confirm evidence.', actor='fixture',
        selector={'type': 'FragmentSelector', 'value': 'supplier-work'})
    # Use the production history follower with the recorded HTTP fixture. No manual bump after writes.
    monkeypatch.setattr(deck_state, 'history_cursor', container.store().latest_sequence(), raising=False)
    monkeypatch.setattr(deck_state, 'schedule_triage', lambda: None, raising=False)
    stop = Event()
    watcher = Thread(target=FleetState.follow_history, args=(deck_state, stop), daemon=True)
    watcher.start()
    # Disable the supplementary page timer: this test must pass through SSE alone.
    page.add_init_script('''const interval = window.setInterval;
      window.setInterval = (fn, ms, ...args) => ms === 2000 ? 0 : interval(fn, ms, ...args);
      window.documentIdentity = crypto.randomUUID();
      window.liveVersions = [];
      const Source = window.EventSource;
      window.EventSource = class extends Source {
        constructor(...args) { super(...args); this.addEventListener('state', e => liveVersions.push(JSON.parse(e.data).version)); }
      };''')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(demo_page['url'])
        page.wait_for_function('() => window.liveVersions.length > 0')
        identity = page.evaluate('documentIdentity')
        version = page.evaluate('liveVersions.at(-1)')
        thread = page.locator(f'#thread-{comment["id"]}')
        expect(thread).to_be_visible()
        thread.get_by_label('Reply').fill('My unsent reply survives a live refresh.')
        page.locator('#supplier-work [data-comment-block]').click()
        page.get_by_label('Comment', exact=True).fill('My unsent draft survives a live refresh.')
        container.work().set(demo_page['work'], next_step='Accept the verified supplier mapping.', actor='fixture')
        container.decisions().answer(comment['id'], 'Mapping verified through the library.', actor='fixture')
        expect(page.locator('#supplier-work')).to_contain_text('Accept the verified supplier mapping.', timeout=10000)
        expect(thread.get_by_role('button', name='Resolved · Show')).to_be_visible(timeout=10000)
        thread.get_by_role('button', name='Resolved · Show').click()
        expect(thread.locator('.answer')).to_contain_text('Mapping verified through the library.', timeout=10000)
        expect(thread).to_have_class(re.compile(r'(?=.*\bresolved\b)(?=.*\bexpanded\b)(?=.*\bactive\b)'))
        expect(thread.get_by_label('Reply')).to_have_value('My unsent reply survives a live refresh.')
        expect(page.get_by_label('Comment', exact=True)).to_have_value('My unsent draft survives a live refresh.')
        assert page.evaluate('documentIdentity') == identity
        assert page.evaluate('liveVersions.at(-1)') > version
        page.get_by_role('button', name='Cancel', exact=True).click()
        # Replaced directive buttons still select their stable block.
        page.locator('#supplier-work [data-comment-block]').click()
        expect(page.locator('#comment-anchor')).to_contain_text('Block: supplier-work')
        page.get_by_role('button', name='Cancel', exact=True).click()
        destination = os.environ.get('FLEET_PAGE_SCREENSHOT_DIR')
        if destination:
            page.screenshot(path=str(Path(destination) / 'M3-live-updates.png'), full_page=True)
        assert errors == []
    finally:
        page.goto('about:blank')
        stop.set()
        watcher.join(timeout=5)
        assert not watcher.is_alive()


@pytest.mark.browser
def test_margin_interaction_and_evidence(page, demo_page):
    from uuid import uuid4
    from playwright.sync_api import expect

    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_viewport_size(dict(width=1440, height=1100))
    ids = []
    for body, selector in [
        ('Should the shared view include archived suppliers?', {'type': 'TextQuoteSelector', 'exact': 'A shared view'}),
        ('The contract mapping is ready for review.', {'type': 'FragmentSelector', 'value': 'supplier-work'}),
        ('Please confirm the historical scope.', {'type': 'FragmentSelector', 'value': 'supplier-question'}),
    ]:
        ids.append(page_post(demo_page, 'comment-text', dict(revision=demo_page['revision'],
            comment_id=str(uuid4()), body=body, selector=selector))['id'])
    page.goto(demo_page['url'])
    expect(page.locator('.thread')).to_have_count(3)
    assert page.get_by_text('Comments and answers', exact=True).count() == 0
    assert page.locator('#page-connection').inner_text() == ''
    icon = page.locator('#supplier-work [data-comment-block]')
    expect(icon).to_have_css('opacity', '0')
    page.locator('#supplier-work').hover()
    expect(icon).to_have_css('opacity', '1')
    icon.focus()
    expect(icon).to_be_focused()
    assert page.evaluate('''() => {
      const cards = [...document.querySelectorAll('.thread')].map(el => el.getBoundingClientRect());
      return cards.every((a, i) => cards.every((b, j) => i === j || a.bottom <= b.top || b.bottom <= a.top));
    }''')
    destination = os.environ.get('FLEET_PAGE_SCREENSHOT_DIR')
    def shot(name):
        if destination:
            page.screenshot(path=str(Path(destination) / name), full_page=name != 'margin-mobile-sheet.png')
    shot('margin-desktop.png')
    # Real pointer click on highlighted text: Recogito's overlay has pointer-events:none.
    highlight = page.locator('.r6o-annotation').first.bounding_box()
    page.mouse.click(highlight['x'] + highlight['width'] / 2, highlight['y'] + highlight['height'] / 2)
    first = page.locator(f'#thread-{ids[0]}')
    expect(first).to_have_class(re.compile(r'\bactive\b'))
    expect(page.locator('.annotation-active')).to_be_visible()
    page.locator(f'#thread-{ids[1]}').click()
    expect(page.locator('#supplier-work')).to_have_class(re.compile(r'\banchor-active\b'))
    first.click()
    expect(page.locator('.annotation-active')).to_be_visible()
    select_prose(page, 'the active supplier slice')
    page.get_by_role('button', name='Comment on selection').click()
    expect(page.locator('#comment-form textarea')).to_have_count(1)
    expect(page.get_by_label('Who must respond?')).to_have_value('user')
    page.get_by_label('Comment', exact=True).fill('Could we add the acceptance date?')
    shot('margin-composer.png')
    page.keyboard.press('Escape')
    expect(page.locator('#comment-composer')).to_be_hidden()
    icon.click()
    page.get_by_label('Comment', exact=True).fill('Please check the acceptance date.')
    page.get_by_label('Who must respond?').select_option('agent')
    page.keyboard.press('Control+Enter')
    expect(page.locator('.thread')).to_have_count(4)
    with urlopen(f'{demo_page["base"]}/api/pages/{demo_page["project"]}/supplier-migration') as response:
        assert next(t for t in json.load(response)['threads'] if t['annotation']['body'] == 'Please check the acceptance date.')['owner'] == 'agent'
    first.get_by_role('button', name='Resolve', exact=True).click()
    expect(first.get_by_role('button', name='Resolved · Show')).to_be_visible()
    expect(first.locator('.thread-body')).to_be_hidden()
    shot('margin-resolved.png')
    first.get_by_role('button', name='Resolved · Show').click()
    expect(first.locator('.thread-body')).to_be_visible()
    page.set_viewport_size(dict(width=390, height=844))
    page.get_by_role('button', name='Close comments').click()
    expect(page.locator('#page-margin')).to_be_hidden()
    page.get_by_role('button', name='2 comments', exact=True).click()
    expect(page.locator('#page-threads .sheet-visible')).to_have_count(2)
    expect(page.locator(f'#thread-{ids[1]}')).to_be_visible()
    page.get_by_role('button', name='Close comments').click()
    page.locator('[data-prose-node] + .anchor-badge').click()
    expect(first).to_be_visible()
    expect(page.get_by_role('button', name='Close comments')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    shot('margin-mobile-sheet.png')
    page.get_by_role('button', name='Close comments').click()
    expect(page.locator('#page-margin')).to_be_hidden()
    assert errors == [], errors
    page.goto('about:blank')


@pytest.mark.browser
def test_cli_replies_stay_open_through_sse_and_explicit_resolution(page, demo_page, deck_state, monkeypatch):
    from threading import Event, Thread
    from uuid import uuid4
    from playwright.sync_api import expect
    from fleet.services.live import FleetState
    from fleet_cli import cli

    container = configured_container()
    comment = container.page_change(project=demo_page['project'], slug='supplier-migration', operation='comment',
        revision=demo_page['revision'], comment_id=str(uuid4()), headline='Contract evidence',
        body='Please verify the supplier contract evidence.', reason='User must review evidence.', actor='user',
        selector={'type': 'FragmentSelector', 'value': 'supplier-work'})
    monkeypatch.setattr(deck_state, 'history_cursor', container.store().latest_sequence(), raising=False)
    monkeypatch.setattr(deck_state, 'schedule_triage', lambda: None, raising=False)
    stop = Event()
    watcher = Thread(target=FleetState.follow_history, args=(deck_state, stop), daemon=True)
    watcher.start()
    page.add_init_script('''const interval = window.setInterval;
      window.setInterval = (fn, ms, ...args) => ms === 2000 ? 0 : interval(fn, ms, ...args);
      window.documentIdentity = crypto.randomUUID(); window.liveVersions = [];
      const Source = window.EventSource;
      window.EventSource = class extends Source {
        constructor(...args) { super(...args); this.addEventListener('state', e => liveVersions.push(JSON.parse(e.data).version)); }
      };''')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.set_viewport_size(dict(width=1440, height=1100))
        page.goto(demo_page['url'])
        page.wait_for_function('() => liveVersions.length > 0')
        identity = page.evaluate('documentIdentity')
        thread = page.locator(f'#thread-{comment["id"]}')
        thread.click()
        thread.get_by_label('Reply', exact=True).fill('I will compare the signed contracts.')
        thread.get_by_role('button', name='Reply', exact=True).click()
        expect(thread.locator('.answer')).to_have_count(1)
        thread.get_by_label('Reply').fill('An unsent draft stays here.')
        version = page.evaluate('liveVersions.at(-1)')
        for actor, text in [('agent', 'All active suppliers match the signed mapping.'),
                            ('user', 'Thanks. Keep this open until acceptance.')]:
            cli.main(['attention', 'reply', comment['id'], text, '--actor', actor], container=container)
        expect(thread.locator('.answer')).to_have_count(3, timeout=10000)
        assert thread.locator('.answer p').all_text_contents() == [
            'I will compare the signed contracts.', 'All active suppliers match the signed mapping.',
            'Thanks. Keep this open until acceptance.']
        expect(thread).not_to_have_class(re.compile(r'\bresolved\b'))
        expect(thread.get_by_label('Reply')).to_have_value('An unsent draft stays here.')
        assert container.attention().get(comment['id']).state == 'open'
        assert container.decisions().list() == []
        assert page.evaluate('documentIdentity') == identity
        assert page.evaluate('liveVersions.at(-1)') > version
        destination = os.environ.get('FLEET_PAGE_SCREENSHOT_DIR')
        if destination:
            page.screenshot(path=str(Path(destination) / 'thread-three-replies-open.png'), full_page=True)
        thread.get_by_role('button', name='Resolve', exact=True).click()
        expect(thread).to_have_class(re.compile(r'\bresolved\b'))
        expect(thread.get_by_role('button', name='Resolved · Hide')).to_be_visible()
        expect(thread.locator('.answer')).to_have_count(3)
        if destination:
            page.screenshot(path=str(Path(destination) / 'thread-resolved.png'), full_page=True)
        thread.get_by_role('button', name='Re-open', exact=True).click()
        expect(thread).not_to_have_class(re.compile(r'\bresolved\b'))
        assert container.attention().get(comment['id']).state == 'open'
        thread.get_by_label('Reply').fill('Accepted the verified mapping.')
        thread.get_by_role('button', name='Answer & resolve', exact=True).click()
        expect(thread).to_have_class(re.compile(r'\bresolved\b'))
        assert container.decisions().list()[0].answer == 'Accepted the verified mapping.'
        assert errors == []
    finally:
        page.goto('about:blank')
        stop.set()
        watcher.join(timeout=5)
