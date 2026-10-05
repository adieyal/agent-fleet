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
