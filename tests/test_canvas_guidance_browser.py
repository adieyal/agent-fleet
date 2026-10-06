"""The reader edits the Fleet constitution and shows its current body."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_web_canvas import deck, post


@pytest.mark.browser
def test_reader_edits_shared_guidance(page, deck, request):
    post(deck, '/api/canvas/init', {'space': deck.project, 'north_star': 'Share Fleet guidance'})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.goto(deck.url + '/canvas/' + deck.project)
    page.get_by_role('radio', name='Reader', exact=True).click()
    expect(page.get_by_text("Edits save a new version of Fleet's project constitution.", exact=False)).to_be_visible()
    page.locator('button[data-act="editNorthStar"]').click()
    page.locator('#ns-r').fill('One constitution for every client')
    page.get_by_role('button', name='Save as a new version', exact=True).click()
    expect(page.get_by_role('paragraph').and_(page.get_by_text('One constitution for every client', exact=True))).to_be_visible()
    page.get_by_text('Read the project constitution', exact=True).click()
    expect(page.locator('details pre')).to_contain_text('One constitution for every client')
    assert errors == []
    destination = request.config.getoption('--shots')
    if destination:
        target = Path(destination)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / 'canvas-shared-guidance.png'), full_page=True)
