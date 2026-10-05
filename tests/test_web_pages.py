"""Page routes over confirmed management records, including a real browser view."""
import json
import os
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
    form.get_by_label('Headline', exact=True).fill(headline)
    form.get_by_label('Comment', exact=True).fill(body)
    form.get_by_label('Why must this owner act?', exact=True).fill('The user must decide the requested scope.')
    destination = os.environ.get('FLEET_PAGE_SCREENSHOT_DIR')
    if destination and headline == 'History scope':
        page.locator('#comment-composer').screenshot(path=str(Path(destination) / 'M2-comment-composer.png'))
    form.get_by_role('button', name='Create comment', exact=True).click()


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
    expect(page.locator('#comment-composer')).to_be_visible()
    expect(page.locator('#comment-anchor')).to_contain_text('A shared view')
    fill_comment(page, 'History scope', 'Should this view include historical suppliers?')
    thread = page.locator('.thread').filter(has=page.get_by_role('heading', name='History scope', exact=True))
    expect(thread).to_be_visible()
    expect(thread).to_contain_text('Attached to selected prose')
    thread.get_by_label('Answer', exact=True).fill('Current suppliers only.')
    thread.get_by_role('button', name='Answer and resolve', exact=True).click()
    expect(thread.locator('.answer')).to_contain_text('Current suppliers only.')
    page.reload()
    expect(page.locator('.thread .answer')).to_contain_text('Current suppliers only.')
    page.locator('#supplier-work').get_by_role('button', name='Comment on block', exact=True).click()
    fill_comment(page, 'Contract evidence', 'Please verify the mapping evidence.')
    block = page.locator('.thread').filter(has=page.get_by_role('heading', name='Contract evidence', exact=True))
    expect(block).to_contain_text('Block: supplier-work')
    item_id = block.get_attribute('data-attention-id')
    cli.main(['answer', item_id, 'Verified by the CLI.'], container=configured_container())
    expect(block.locator('.answer')).to_contain_text('Verified by the CLI.', timeout=10000)
    expect(block).to_contain_text('resolved')
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
    thread.get_by_role('button', name='Follow up', exact=True).click()
    fill_comment(page, 'Follow-up after edit', 'Keep the old context visible.')
    followup = page.locator('.thread').filter(has=page.get_by_role('heading', name='Follow-up after edit', exact=True))
    expect(followup).to_be_visible()
    expect(followup.get_by_role('link', name='Parent comment')).to_have_attribute('href', f'#thread-{result["id"]}')
    destination = os.environ.get('FLEET_PAGE_SCREENSHOT_DIR')
    if destination:
        page.screenshot(path=str(Path(destination) / 'M2-detached-thread.png'), full_page=True)
    page.goto('about:blank')
