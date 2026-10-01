"""Browser smoke tests: the deck against the recorded fleet, at desktop and phone widths."""

import io
import json
import re
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.request import urlopen

import pytest
from PIL import Image, ImageStat
from playwright.sync_api import Browser, Page, expect

from browser_clock import advance_until
from fleet.composition import (open_attention, open_decisions, open_execution, open_library, open_records,
                               open_store, open_work)
from fleet.modules.execution import JobObservation
from fleet.modules.work import EvidenceSpecification

VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "narrow": {"width": 390, "height": 844}}
# The deck ages jobs against the browser clock; pin it to the moment the fixture was recorded.
PIN_CLOCK = """
let offset = %d * 1000 - Date.now();
const realNow = Date.now.bind(Date);
Date.now = () => realNow() + offset;
window.advanceClock = seconds => { offset += seconds * 1000; };
window.resetClock = () => { offset = %d * 1000 - realNow(); };
"""
FINISHED = {"done", "cancelled"}
BLOCKED = {"failed", "stalled"}
ASKING = "home:8e1f0c42-2b7d-4a55-9c1e-7f3a2d6b9e10"     # idle two minutes, with a decision waiting
REVIEWING = "worker:019a7c3e-55d1-7b20-a8f4-3c9e0d1b2a67"  # idle about eighteen minutes
BACKGROUND = {"invoice-parser"}                            # the fixture's one room in the background


def test_animation_clock_uses_real_time_until_explicitly_stepped(browser: Browser, base_url: str) -> None:
    page = browser.new_page()
    page.goto(base_url + "/api/state")
    assert page.evaluate("""async () => {
        const clock = await import('/js/clock.js');
        let real = 100;
        performance.now = () => real;
        const readings = [clock.animationNow(), clock.isStepping()];
        real = 200;
        readings.push(clock.animationNow());
        clock.advanceClock(0);
        real = 900;
        readings.push(clock.animationNow(), clock.isStepping());
        clock.advanceClock(1.5);
        readings.push(clock.animationNow());
        return readings;
    }""") == [100, False, 200, 200, True, 1700]
    page.close()


def on_the_floor(fixture_data: dict[str, Any]) -> set[str]:
    """Every job and session the deck draws: finished jobs have left, blocked ones are under their lantern, and a
    background room has no crew."""
    return {f"{host['name']}:{item['id']}" for host in fixture_data["hosts"]
            for item in host["jobs"] + host["sessions"]
            if item["status"] not in FINISHED | BLOCKED and item["project"] not in BACKGROUND}


@dataclass
class Deck:
    page: Page
    errors: list[str] = field(default_factory=list)


@pytest.fixture(scope="module")
def loaded_decks(browser: Browser, base_url: str,
                 fixture_data: dict[str, Any]) -> Iterator[Callable[[str, str], Deck]]:
    decks: dict[tuple[str, str], Deck] = {}

    def load(viewport: str, motion: str) -> Deck:
        key = viewport, motion
        if key not in decks:
            context = browser.new_context(viewport=VIEWPORTS[viewport], reduced_motion=motion)
            context.add_init_script(PIN_CLOCK % (fixture_data["time"], fixture_data["time"]))
            page = context.new_page()
            deck = decks[key] = Deck(page)
            page.on("console", lambda message: message.type == "error" and deck.errors.append(message.text))
            page.on("pageerror", lambda error: deck.errors.append(str(error)))
            page.goto(base_url + "/")
            page.wait_for_function(f"window.fleetDeck && (fleetDeck.advanceTime(0), fleetDeck.agents().length === {len(on_the_floor(fixture_data))})")
        return decks[key]

    yield load
    for deck in decks.values():
        deck.page.context.close()


@pytest.fixture(scope="module", params=list(VIEWPORTS))
def deck(request: pytest.FixtureRequest, loaded_decks: Callable[[str, str], Deck]) -> Deck:
    """One page per viewport, loaded once; each test leaves it with nothing open."""
    return loaded_decks(request.param, "reduce")


@pytest.fixture
def changed_deck(request: pytest.FixtureRequest, loaded_decks: Callable[[str, str], Deck],
                 base_url: str) -> Iterator[Deck]:
    deck = loaded_decks("desktop", getattr(request, "param", "reduce"))
    original = finish_jobs(base_url, {})
    try:
        yield deck
    finally:
        # the test has asserted on its own errors; a failed one must not fail every later test on this page
        deck.errors.clear()
        deck.page.set_viewport_size(VIEWPORTS["desktop"])
        deck.page.keyboard.press("Escape")
        deck.page.keyboard.press("Escape")
        deck.page.evaluate("""doc => {
            resetClock();
            fleetDeck.apply(doc);
            fleetDeck.lookAtRoom(null);
            fleetDeck.advanceTime(0);
        }""", original)


def test_every_project_gets_a_room(deck: Deck, fixture_data: dict[str, Any]) -> None:
    projects = {item["project"] for host in fixture_data["hosts"] for item in host["jobs"] + host["sessions"]}
    rooms = rooms_by_name(deck.page)
    assert set(rooms) == projects
    assert rooms["invoice-parser"]["label"] == "Invoice parser"
    assert deck.errors == []


def test_seeded_project_bench_by_floor_id(changed_deck: Deck, deck_state, monkeypatch) -> None:
    import runpy
    import sys
    from pathlib import Path
    from fleet.composition import open_workspace

    store = open_store()
    workspace = open_workspace(store)
    workspace.move_in(['worker'], 'restoke', name='Restoke V2')
    project = workspace.resolve_project('Restoke V2')
    assert project in workspace.floors_snapshot()
    script = Path(__file__).parents[1] / 'scripts/seed_supplier_slice.py'
    monkeypatch.setattr(sys, 'argv', [str(script), '--project', project])
    runpy.run_path(str(script), run_name='__main__')
    monkeypatch.setattr(deck_state, 'store', store)
    monkeypatch.setattr(deck_state, 'attention', open_attention(store))
    page = changed_deck.page
    page.evaluate('(project) => fleetDeck.enterFloor(project)', project)
    expect(page.get_by_role('button', name='V2 frontend overhaul', exact=True)).to_be_visible()
    page.get_by_role('button', name='V2 frontend overhaul', exact=True).click()
    page.get_by_role('button', name='Slice 6: supplier imports', exact=True).click()
    expect(page.locator('#benchRoute')).to_have_attribute('data-level', 'bench')
    expect(page.locator('#benchRoute')).to_contain_text('Slice 6: supplier imports')
    page.evaluate('fleetDeck.enterFloor(null)')


def test_bench_real_endpoint(changed_deck: Deck, deck_state, monkeypatch, tmp_path) -> None:
    store = open_store()
    work = open_work(store)
    monkeypatch.setattr(deck_state, 'store', store)
    monkeypatch.setattr(deck_state, 'attention', open_attention(store))
    epic = work.add(project='bench-contract', title='Contract room', goal='Deliver', kind='epic', actor='user')
    milestone = work.add(project='bench-contract', title='Contract slice', goal='Deliver',
                         kind='milestone', parent=epic.id, actor='user')
    for title in ['Gather evidence', 'Review results']:
        task = work.add(project='bench-contract', title=title, goal=title, parent=milestone.id, actor='user')
    work.add_criterion(milestone.id, text='checked evidence', verification='checked', actor='user',
                       specification=EvidenceSpecification('test:bench', 'passed'))
    for kind in ['judged', 'accepted']:
        criterion = work.add_criterion(milestone.id, text=f'{kind} evidence', verification=kind, actor='user')
    work.meet(criterion.id, actor='user')
    execution = open_execution(store)
    run = execution.link('worker', 'bench-job', task.id, actor='user')
    now = store.clock()
    execution.observe('worker', JobObservation('bench-job', 'running', 'codex', now, None, now,
                      current_action='read', action_observed_at=now))
    deck_state.attention.raise_item(project='bench-contract', work_item=milestone.id, kind='decision',
        owner='user', source='manual', source_reference='bench-question', headline='Accept evidence?',
        context_reference='work:' + milestone.id, actor='user')
    open_library(store).index_run(run=run.id, work_item=task.id, kind='report', title='Contract report',
                                location='fleet://worker/bench-job/report', availability='available')
    open_library(store).index_run(run=run.id, work_item=task.id, kind='report', title='Step 1 report',
                                location='fleet://worker/home/u/.fleet/jobs/bench-job/result-0.md', availability='available')
    repo = tmp_path / 'management'
    subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True, timeout=10)
    open_records(store).register('bench-contract', repo, actor='user')
    work.set_summary(milestone.id, purpose='Find suppliers', done='Evidence gathered', doing='Review results',
                     next='Accept results', authoring_role='user', actor='user')

    page = changed_deck.page
    page.evaluate("fleetDeck.enterFloor('bench-contract')")
    page.get_by_role('button', name='Contract room', exact=True).click()
    page.get_by_role('button', name='Contract slice', exact=True).click()
    bench = page.locator('#benchRoute')
    expect(bench).to_have_attribute('data-level', 'bench')
    expect(bench.locator('[data-task]')).to_have_text(['Review results', 'Gather evidence'])
    assert bench.locator('[data-task]').evaluate_all('(els) => els.map(e => e.dataset.lane)') == ['doing', 'next']
    for kind, state in [('checked', 'unmet'), ('judged', 'unmet'), ('accepted', 'met')]:
        light = bench.locator(f'[data-verification="{kind}"]')
        expect(light).to_have_attribute('data-state', state)
        expect(light).to_have_attribute('title', f'{kind} evidence')
    expect(bench.get_by_text('1 / 3', exact=True)).to_be_visible()
    expect(bench.locator('[data-agent]')).to_have_attribute('data-agent', run.id)
    expect(bench.locator('[data-agent] .glyph')).to_have_attribute('data-action', 'read')
    expect(bench.locator('[data-lantern]')).to_be_visible()
    bench.locator('[data-tray] summary').click()
    expect(bench.locator('[data-tray]')).to_contain_text('Contract report')
    expect(bench.locator('[data-tray]')).to_contain_text('worker · report')   # the host and file; the full location copies
    expect(bench.locator('[data-copy]').first).to_have_attribute('data-copy', 'fleet://worker/bench-job/report')
    expect(bench.locator('[data-tray]')).not_to_contain_text('fleet://')
    expect(bench.locator('[data-availability]').first).to_have_attribute('data-availability', 'available')
    # a step report opens in the reader; a location that names no step report stays text
    expect(bench.locator('[data-open-report]')).to_have_count(1)
    expect(bench.locator('[data-open-report]')).to_have_attribute('data-title', 'Step 1 report')
    expect(bench.locator('[data-open-report]')).to_have_attribute('data-doc', 'report-0')
    bench.locator('[data-open-report]').click()
    expect(page.locator('#reader')).to_be_visible()
    expect(page.locator('#rdTitle')).to_have_text('Step 1 report')
    # the seeded run has no job on any host, so the reader's fetch 404s; that is expected here, not a page error.
    # Wait for the reader to say so: the browser logs the 404 only once the fetch lands, after the reader opens.
    expect(page.locator('#reader .rd-error')).to_contain_text('Couldn’t open this document')
    page.keyboard.press('Escape')
    changed_deck.errors[:] =[e for e in changed_deck.errors if 'Not Found' not in e and '404' not in e]
    expect(page.locator('#reader')).to_be_hidden()
    bench.locator('[data-briefing]').click()
    for text in ['Find suppliers', 'Evidence gathered', 'Review results', 'Accept results']:
        expect(bench.locator('[data-summary]')).to_contain_text(text)
    page.evaluate('fleetDeck.enterFloor(null)')


@pytest.fixture
def route_migration(deck_state, monkeypatch) -> dict[str, Any]:
    """Restoke V2's shape: epic Route migration inside epic V2 frontend overhaul, six milestones, three complete."""
    store = open_store()
    work = open_work(store)
    monkeypatch.setattr(deck_state, 'store', store)
    monkeypatch.setattr(deck_state, 'attention', open_attention(store))
    add = lambda title, goal, **fields: work.add(project='restoke-v2', title=title, goal=goal, actor='user', **fields)
    overhaul = add('V2 frontend overhaul', 'Rebuild the V2 frontend on one design system.', kind='epic')
    routes = add('Route migration', 'Move every page to the new router. Legacy routes go last.',
                 kind='epic', parent=overhaul.id)
    work.add_criterion(routes.id, text='Legacy router deleted', verification='accepted', actor='user')
    next_steps = {4: 'Port the supplier list', 6: 'Delete router.js'}
    milestones = [add(f'{n}. Milestone {n}', f'Deliver milestone {n}. Details follow.', kind='milestone',
                      parent=routes.id, next_step=next_steps.get(n)) for n in range(1, 7)]
    for milestone in milestones[:3]:
        work.set(milestone.id, condition='complete', actor='user')
    work.set(milestones[5].id, condition='blocked', actor='user')
    task = add('Port supplier list', 'Port it', parent=milestones[3].id)
    redirect = add('Fix redirect loop', 'Fix it', parent=routes.id)
    parked = add('Tidy styles', 'Tidy them', parent=routes.id, next_step='Wait for tokens')
    work.set(parked.id, condition='on hold', actor='user')
    execution = open_execution(store)
    execution.link('home', 'route-job', task.id, actor='user')
    now = store.clock()
    execution.observe('home', JobObservation('route-job', 'running', 'codex', now, None, now))
    deck_state.attention.raise_item(project='restoke-v2', work_item=milestones[4].id, kind='decision',
        owner='user', source='manual', source_reference='route-question', headline='Keep order URLs?',
        context_reference='work:' + milestones[4].id, actor='user')
    return {'routes': routes, 'milestones': milestones, 'redirect': redirect, 'store': store}


def test_epic_cards_summarise_nested_route_migration(changed_deck: Deck, route_migration,
                                                     request: pytest.FixtureRequest) -> None:
    routes = route_migration['routes']
    page = changed_deck.page
    page.evaluate("fleetDeck.enterFloor('restoke-v2')")
    cards = page.locator('[data-epic-card]')
    expect(cards).to_have_count(2)
    parent, child = cards.nth(0), cards.nth(1)
    expect(parent.locator('[data-now]')).to_have_text('Now: 1 running: Port supplier list (home)')
    expect(parent.locator('[data-milestones]')).to_have_text('No milestones or tasks recorded')
    expect(parent.locator('[data-next]')).to_have_text('Next: No milestones recorded')
    expect(parent.locator('[data-child-epics]')).to_have_text('Epics: Route migration')
    expect(parent.locator('[data-attention-count]')).to_have_attribute('data-attention-count', '2')

    expect(child.locator('[data-parent-epic]')).to_have_text('in V2 frontend overhaul')
    assert child.evaluate('e => e.getBoundingClientRect().left') > parent.evaluate('e => e.getBoundingClientRect().left')
    expect(child.locator('[data-goal]')).to_have_text('Move every page to the new router.')
    expect(child.locator('[data-goal]')).to_have_attribute('title', routes.goal)
    expect(child.locator('[data-milestones] small')).to_have_text('3 of 6 milestones complete · 1 running · 1 blocked')
    segments = child.locator('[data-progress-bar] i')
    assert segments.evaluate_all('els => els.map(e => [e.dataset.segment, e.style.flexGrow])') == [
        ['complete', '3'], ['active', '1'], ['blocked', '1'], ['next', '1']]
    expect(child.locator('[data-upcoming]')).to_have_text([
        '4. Milestone 4 — Port the supplier list', '5. Milestone 5 — Next step not recorded',
        '6. Milestone 6 — Delete router.js'])
    expect(child.locator('[data-now]')).to_have_text('Now: 1 running: Port supplier list (home)')
    expect(child.locator('[data-attention-count]')).to_have_text('2 open decisions or blockers')
    shoot(request, page, 'epic-cards')
    card, title = child.bounding_box(), child.locator('[data-epic]').bounding_box()
    assert card['y'] <= title['y'] and title['y'] + title['height'] <= card['y'] + card['height']

    child.get_by_role('button', name='Route migration', exact=True).click()
    expect(page.locator('#benchRoute')).to_have_attribute('data-level', 'room')
    expect(page.locator('[data-slice]')).to_have_count(6)
    page.evaluate('fleetDeck.enterFloor(null)')
    assert changed_deck.errors == []


def test_epic_page_lists_milestones_tasks_and_child_epics(changed_deck: Deck, route_migration) -> None:
    routes, milestones = route_migration['routes'], route_migration['milestones']
    page = changed_deck.page
    route = page.locator('#benchRoute')
    page.evaluate("fleetDeck.enterFloor('restoke-v2')")
    page.get_by_role('button', name='Route migration', exact=True).click()
    epic = page.locator('[data-epic-page]')
    expect(route).to_have_attribute('data-level', 'room')
    expect(epic.get_by_role('heading', level=2)).to_have_text('Route migration')
    expect(epic.locator('[data-goal]')).to_have_text(routes.goal)
    expect(epic.locator('[data-epic-criteria] li')).to_have_text(['Legacy router deleted accepted · unmet'])
    expect(epic.locator('[data-progress]')).to_have_text('3 of 6 milestones')
    items = epic.locator('[data-plan-item]')
    assert items.evaluate_all('els => els.map(e => e.dataset.status)') == [
        'complete', 'complete', 'complete', 'active', 'next', 'blocked', 'next', 'on hold']
    expect(items.locator('[data-slice]')).to_have_text([f'{n}. Milestone {n}' for n in range(1, 7)])
    expect(items.nth(3)).to_contain_text('Deliver milestone 4.')
    expect(items.nth(3)).not_to_contain_text('Details follow')
    expect(items.locator('[data-next-step]')).to_have_text(
        ['Next step not recorded'] * 3 + ['Next: Port the supplier list', 'Next step not recorded',
         'Next: Delete router.js', 'Next step not recorded', 'Next: Wait for tokens'])
    expect(items.nth(7)).to_contain_text('Tidy styles')
    expect(items.nth(7).locator('[data-slice]')).to_have_count(0)
    glyphs = items.locator('[data-glyph] svg').evaluate_all('els => els.map(e => e.innerHTML)')
    assert len(set(glyphs)) == 5
    expect(items.nth(5).locator('[data-glyph]')).to_have_attribute('aria-label', 'blocked')

    page.get_by_role('button', name='4. Milestone 4', exact=True).click()
    expect(route).to_have_attribute('data-level', 'bench')
    expect(route.locator('[data-task]')).to_have_text(['Port supplier list'])
    page.keyboard.press('Escape')
    expect(route).to_have_attribute('data-level', 'room')
    page.keyboard.press('Escape')
    expect(route).to_have_attribute('data-level', 'floor')
    expect(page.locator('[data-epic-card]')).to_have_count(2)

    page.get_by_role('button', name='V2 frontend overhaul', exact=True).click()
    expect(epic.locator('[data-progress]')).to_have_text('Progress not recorded')
    expect(epic).to_contain_text('No milestones recorded')
    epic.locator('[data-child-epics]').get_by_role('button', name='Route migration').click()
    expect(epic.get_by_role('heading', level=2)).to_have_text('Route migration')
    page.locator('[data-back-floor]').click()
    expect(route).to_have_attribute('data-level', 'floor')
    page.evaluate('fleetDeck.enterFloor(null)')
    assert changed_deck.errors == []


@pytest.fixture
def supplier_migration(deck_state, monkeypatch) -> None:
    """V2 frontend overhaul with a milestone of its own and workstream Supplier migration at 6 of 7."""
    store = open_store()
    work = open_work(store)
    monkeypatch.setattr(deck_state, 'store', store)
    add = lambda title, **fields: work.add(project='v2-overhaul', title=title, goal=f'{title}.', actor='user', **fields)
    overhaul = add('V2 frontend overhaul', kind='epic')
    add('Design tokens', kind='milestone', parent=overhaul.id)
    stream = add('Supplier migration', kind='workstream', parent=overhaul.id)
    topics = ['routes', 'list', 'detail', 'search', 'exports', 'supplier imports', 'cleanup']
    for n, topic in enumerate(topics, 1):
        slice_ = add(f'Slice {n}: {topic}', kind='milestone', parent=stream.id)
        if n != 6:
            work.set(slice_.id, condition='complete', actor='user')
    add('Order migration', kind='workstream', parent=overhaul.id)


def test_epic_card_and_page_name_each_workstream(changed_deck: Deck, supplier_migration) -> None:
    page = changed_deck.page
    route = page.locator('#benchRoute')
    page.evaluate("fleetDeck.enterFloor('v2-overhaul')")
    card = page.locator('[data-epic-card]')
    expect(card.locator('[data-milestones]')).to_have_text('6 of 8 milestones')
    expect(card.locator('[data-workstream]')).to_have_text([
        'Supplier migration 6 of 7 milestones · next Slice 6: supplier imports',
        'Order migration No milestones recorded'])

    card.get_by_role('button', name='V2 frontend overhaul').click()
    epic = page.locator('[data-epic-page]')
    expect(epic.locator('[data-progress]')).to_have_text('6 of 8 milestones')   # the card's count, workstreams included
    expect(epic.locator('[data-plan-list]').first.locator('[data-slice]')).to_have_text(['Design tokens'])
    stream = epic.get_by_role('region', name='Supplier migration')
    expect(stream.get_by_role('heading', level=4)).to_have_text(
        'Supplier migration 6 of 7 milestones · next Slice 6: supplier imports')
    expect(stream.locator('[data-slice]')).to_have_count(7)
    expect(stream.locator('[data-plan-item][data-status="next"]')).to_have_text(re.compile('Slice 6: supplier imports'))
    expect(epic.get_by_role('region', name='Order migration')).to_contain_text('No milestones recorded')
    assert epic.evaluate('e => e.textContent.indexOf("Design tokens") < e.textContent.indexOf("Supplier migration")')
    stream.get_by_role('button', name='Slice 6: supplier imports').click()
    expect(route).to_have_attribute('data-level', 'bench')
    page.evaluate('fleetDeck.enterFloor(null)')
    assert changed_deck.errors == []


def test_panel_breadcrumb_names_the_linked_work_and_opens_it(changed_deck: Deck, route_migration,
                                                              base_url: str) -> None:
    milestone = route_migration['milestones'][2]
    open_execution(open_store()).link('home', 'a1c3e9', milestone.id, actor='user')
    with urlopen(base_url + '/api/state', timeout=5) as response:
        doc = json.load(response)
    jobs = {(host['name'], item['id']): item for host in doc['hosts'] for item in host['jobs'] + host['sessions']}
    work = jobs['home', 'a1c3e9']['work']
    assert work['project'] == 'restoke-v2'
    assert [(node['kind'], node['title']) for node in work['chain']] == [
        ('epic', 'V2 frontend overhaul'), ('epic', 'Route migration'), ('milestone', '3. Milestone 3')]
    assert jobs['home', ASKING.split(':', 1)[1]]['work'] is None

    page = changed_deck.page
    route = page.locator('#benchRoute')
    page.evaluate('doc => fleetDeck.apply(doc)', doc)
    page.evaluate("fleetDeck.select('home:a1c3e9')")
    crumbs = page.locator('#panel .work-crumbs')
    expect(crumbs).to_have_text('V2 frontend overhaul > Route migration > 3. Milestone 3')
    crumbs.get_by_role('button', name='Route migration').click()
    expect(route).to_have_attribute('data-level', 'room')
    expect(route.locator('[data-epic-page] h2')).to_have_text('Route migration')
    crumbs.get_by_role('button', name='3. Milestone 3').click()
    expect(route).to_have_attribute('data-level', 'bench')
    expect(route.get_by_role('heading', level=2)).to_have_text('3. Milestone 3')
    expect(page.locator('#benchBreadcrumb')).to_contain_text('Route migration')
    page.keyboard.press('Escape')
    expect(route).to_have_attribute('data-level', 'room')
    page.evaluate('fleetDeck.enterFloor(null)')

    page.evaluate(f"fleetDeck.select('{ASKING}')")
    expect(page.locator('#panel .ph h2')).not_to_have_text('')
    expect(page.locator('#panel .work-crumbs')).to_have_count(0)
    page.evaluate("fleetDeck.select('home:b7d042')")
    expect(page.locator('#panel .work-crumbs')).to_have_count(0)
    page.locator('#panel #close').click()
    assert changed_deck.errors == []


def test_lone_milestone_steps_back_to_the_floor(changed_deck: Deck, deck_state, monkeypatch, base_url: str) -> None:
    store = open_store()
    monkeypatch.setattr(deck_state, 'store', store)
    work = open_work(store)
    milestone = work.add(project='lone', title='Lone slice', goal='Deliver', kind='milestone', actor='user')
    work.add(project='lone', title='Solo', goal='Deliver', kind='epic', actor='user')
    open_execution(store).link('home', 'a1c3e9', milestone.id, actor='user')
    with urlopen(base_url + '/api/state', timeout=5) as response:
        doc = json.load(response)
    page = changed_deck.page
    route = page.locator('#benchRoute')
    page.evaluate('doc => fleetDeck.apply(doc)', doc)
    page.evaluate("fleetDeck.select('home:a1c3e9')")
    page.locator('#panel .work-crumbs').get_by_role('button', name='Lone slice').click()
    expect(route).to_have_attribute('data-level', 'bench')
    page.keyboard.press('Escape')
    expect(route).to_have_attribute('data-level', 'floor')
    expect(route.locator('[data-epic-card]')).to_have_count(1)
    page.keyboard.press('Escape')
    expect(route).to_be_hidden()

    page.evaluate("fleetDeck.select('home:a1c3e9')")
    page.locator('#panel .work-crumbs').get_by_role('button', name='Lone slice').click()
    expect(route).to_have_attribute('data-level', 'bench')
    page.locator('[data-back-floor]').click()
    expect(route).to_have_attribute('data-level', 'floor')
    page.evaluate('fleetDeck.enterFloor(null)')
    page.locator('#panel #close').click()
    assert changed_deck.errors == []


def test_a_tasks_title_opens_its_latest_report_and_its_documents_list_by_icon(
        changed_deck: Deck, route_migration, request: pytest.FixtureRequest) -> None:
    store, task = route_migration['store'], route_migration['redirect']
    run = open_execution(store).link('home', 'docs-job', task.id, actor='user')
    jobs = 'fleet://home/home/u/.fleet/jobs/docs-job'
    for kind, title, path in [('brief', 'Step 1 brief', 'brief-0.md'), ('report', 'Step 1: fix it', 'result-0.md'),
                              ('report', 'Step 2: prove it', 'result-1.md'), ('outbox', 'H0-report.md', 'outbox/H0-report.md'),
                              ('trace', 'Run trace', 'events.jsonl')]:
        open_library(store).index_run(run=run.id, work_item=task.id, kind=kind, title=title,
                                      location=f'{jobs}/{path}', availability='available')
    page = changed_deck.page
    page.evaluate("fleetDeck.enterFloor('restoke-v2')")
    page.get_by_role('button', name='Route migration', exact=True).click()
    line = page.locator(f'[data-plan-item="{task.id}"]')
    title = line.get_by_role('button', name='Fix redirect loop', exact=True)
    expect(title).to_have_attribute('data-doc', 'report-1')
    box = title.bounding_box()
    assert box['height'] < 40 and box['width'] > 100, box   # one line, not squeezed into an icon button
    line.locator('[data-step-docs] summary').click()
    docs = line.locator('[data-step-docs] button')
    assert docs.evaluate_all('els => els.map(e => e.dataset.doc)') == [
        'report-1', 'report-0', 'brief-0', 'outbox-H0-report.md']
    expect(docs.locator('svg')).to_have_count(4)
    expect(docs.first).to_have_attribute('title', 'Read this report: Step 2: prove it')
    shoot(request, page, 'task-documents')
    assert changed_deck.errors == []


def test_room_guidance_edit_history_decisions_and_promote(changed_deck: Deck, deck_state, monkeypatch, tmp_path,
                                                         request: pytest.FixtureRequest) -> None:
    """The floor's constitution and an epic's charter are written and edited in place, their versions open in the
    reader, and a decision on the epic's work is promoted into the charter's decisions in force."""
    store = open_store()
    monkeypatch.setattr(deck_state, 'store', store)
    monkeypatch.setattr(deck_state, 'attention', open_attention(store))
    work = open_work(store)
    epic = work.add(project='restoke-v2', title='Transcriber', goal='Read by position.', kind='epic', actor='user')
    task = work.add(project='restoke-v2', title='Liquid Mix', goal='Map it.', parent=epic.id, actor='user')
    repo = tmp_path / 'management'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init'], check=True, capture_output=True, timeout=10)
    open_records(store).register('restoke-v2', repo, actor='user')
    decision = open_decisions(store).record_guided(task.id, actor='codex', question='Store fees as freight?',
                                                   answer='No, as charge lines', principle='Charter: anti-goal 1')
    page = changed_deck.page
    route = page.locator('#benchRoute')
    page.evaluate("fleetDeck.enterFloor('restoke-v2')")
    card = page.locator('[data-constitution-card]')
    expect(card).to_contain_text('Not recorded')
    shoot(request, page, 'guidance-floor')
    card.get_by_role('button', name='Constitution').click()
    expect(route).to_have_attribute('data-level', 'constitution')
    expect(route.locator('[data-guidance="constitution"]')).to_contain_text('No constitution recorded.')
    page.get_by_role('button', name='Write the constitution').click()
    expect(route).to_have_attribute('data-editing', '')
    page.locator('[data-guidance-text]').fill('# Constitution\n\n## Decide yourself\n\n- Test-only fixes.\n')
    page.get_by_role('button', name='Save a new version').click()
    expect(route.locator('[data-guidance-version]')).to_contain_text('version 1 · web-user')
    expect(route.locator('[data-guidance-body] h2')).to_have_text('Decide yourself')
    shoot(request, page, 'guidance-constitution')
    page.keyboard.press('Escape')
    expect(card).to_contain_text('version 1 · web-user')

    page.get_by_role('button', name='Transcriber', exact=True).click()
    expect(route).to_have_attribute('data-level', 'room')
    decisions = route.locator('[data-decision]')
    expect(decisions).to_have_count(1)
    expect(decisions.first).to_contain_text('Principle: Charter: anti-goal 1')
    expect(decisions.first.locator('[data-promote]')).to_have_count(0)   # nothing to promote into yet
    page.get_by_role('button', name='Write the charter').click()
    page.locator('[data-guidance-text]').fill('# Charter\n\n## Decisions in force\n\n1. One.\n')
    shoot(request, page, 'guidance-editor')
    page.get_by_role('button', name='Save a new version').click()
    charter = route.locator('[data-guidance="charter"]')
    expect(charter.locator('[data-inherits]')).to_have_text('Inherits constitution version 1')
    decisions.first.get_by_role('button', name="Add to the charter's decisions in force").click()
    expect(charter.locator('[data-guidance-version]')).to_contain_text('version 2 · web-user')
    expect(charter.locator('[data-guidance-body] li')).to_have_count(2)
    expect(charter.locator('[data-guidance-body] li').nth(1)).to_contain_text(f'decision {decision.id[:8]} by codex')
    expect(decisions.first.locator('[data-in-force]')).to_have_text('In force')
    shoot(request, page, 'room-guidance')
    if request.config.getoption('--shots'):
        page.set_viewport_size(VIEWPORTS['narrow'])
        shoot(request, page, 'room-guidance-narrow')
        page.set_viewport_size(VIEWPORTS['desktop'])

    charter.get_by_role('button', name='Edit the charter').click()
    page.locator('[data-guidance-text]').fill('Edited elsewhere first.')
    open_records(store).write_guidance('restoke-v2', '# Charter\n\nNewer.\n', epic=epic.id, actor='claude')
    page.get_by_role('button', name='Save a new version').click()
    expect(charter.locator('[role="alert"]')).to_contain_text('it is now version 3')
    alert, panel = charter.locator('[role="alert"]').bounding_box(), route.bounding_box()
    assert alert['y'] + alert['height'] <= panel['y'] + panel['height'], (alert, panel)   # not lost under the long editor
    expect(page.locator('[data-guidance-text]')).to_have_value('Edited elsewhere first.')
    shoot(request, page, 'guidance-conflict')
    # the stale save is refused with a 409 on purpose; the browser logs it, which is expected here, not a page error
    changed_deck.errors[:] = [e for e in changed_deck.errors if '409 (Conflict)' not in e]
    page.get_by_role('button', name='Discard the edit').click()
    page.get_by_role('button', name='Versions').click()
    expect(charter.locator('[data-guidance-version]')).to_contain_text('version 3 · claude')
    expect(charter.locator('[data-guidance-versions] li')).to_have_count(3)
    charter.get_by_role('button', name='Read version 1').click()
    expect(page.locator('#reader')).to_be_visible()
    expect(page.locator('#rdTitle')).to_have_text('Charter: Transcriber · version 1')
    expect(page.locator('#rdBody .prose li')).to_have_text(['One.'])
    page.keyboard.press('Escape')
    page.evaluate('fleetDeck.enterFloor(null)')
    assert changed_deck.errors == []


def test_bench_route_steps_out_one_level(changed_deck: Deck) -> None:
    page = changed_deck.page
    page.locator('#viewToggle [data-view="building"]').click()
    page.evaluate('fleetDeck.advanceTime(0)')
    page.locator('.plate[data-floor="1"] .enter').click()
    page.locator('[data-epic]').first.click()
    expect(page.locator('#benchRoute')).to_have_attribute('data-level', 'room')
    page.locator('[data-slice]').first.click()
    expect(page.locator('#benchRoute')).to_have_attribute('data-level', 'bench')
    expect(page.locator('#benchBreadcrumb')).to_contain_text('Supplier slice')
    assert page.evaluate("fleetDeck.textBudget(document.getElementById('benchRoute'))") <= 20
    page.keyboard.press('Escape')
    expect(page.locator('#benchRoute')).to_have_attribute('data-level', 'room')
    expect(page.locator('#benchBreadcrumb')).not_to_contain_text('Supplier slice')
    page.keyboard.press('Escape')
    expect(page.locator('#benchRoute')).to_have_attribute('data-level', 'floor')
    page.keyboard.press('Escape')
    assert page.evaluate('fleetBuilding.current()') is None
    page.locator('#viewToggle [data-view="deck"]').click()


@pytest.mark.parametrize('redact', [False, True])
def test_l3_bench_projection(changed_deck: Deck, base_url: str, redact: bool) -> None:
    page = changed_deck.page
    doc = {
        'id': 'slice', 'title': 'Supplier slice', 'project': 'p-5e1f0a01',
        'tasks': [{'id': lane, 'title': lane, 'lane': lane, 'condition': condition}
                  for lane, condition in [('done', 'complete'), ('doing', 'waiting'), ('next', 'blocked')]],
        'criteria': [{'verification': kind, 'state': state, 'text': kind}
                     for kind in ['checked', 'judged', 'accepted'] for state in ['met', 'unmet']],
        'progress': {'complete': 3, 'total': 6},
        'agents': [{'run': str(i), 'host': 'home', 'status': 'running',
                    'action_glyph': action, 'action_freshness': 'current'}
                   for i, action in enumerate(['read', 'edit', 'test', 'wait', None])],
        'attention': [{'id': 'attention'}],
        'reports': [{'id': 'report', 'title': 'Evidence report', 'availability': 'available',
                     'canonical_location': '/reports/evidence.md'}],
        'summary': {'purpose': 'Find suppliers', 'done': 'Evidence gathered',
                    'doing': 'Review evidence', 'next': 'Accept results'},
    }
    def respond(route):
        if 'slice=' in route.request.url:
            route.fulfill(json=doc)
        else:
            route.fulfill(json={'rooms': [{'id': 'epic', 'title': 'Suppliers', 'depth': 0, 'parent': None,
                                          'goal': 'Find suppliers', 'headline': 'Find suppliers',
                                          'milestones': {'complete': 0, 'total': 1}, 'agents': [],
                                          'upcoming': [], 'children': [], 'attention': [], 'criteria': [],
                                          'progress': {'basis': 'milestones', 'complete': 0, 'total': 1},
                                          'plan': [{'id': 'slice', 'title': 'Supplier slice', 'headline': 'Find',
                                                    'condition': 'none', 'status': 'next', 'next_step': None}],
                                          'tasks': [], 'workstreams': [],
                                          'benches': [{'id': 'slice', 'title': 'Supplier slice'}]}]})
    page.route('**/api/bench?*', respond)
    try:
        if redact:
            page.goto(base_url + '/?redact')
            page.wait_for_function('window.fleetDeck')
        page.evaluate("fleetDeck.enterFloor('p-5e1f0a01')")
        page.locator('[data-epic]').click()
        page.locator('[data-slice]').click()
        bench = page.locator('#benchRoute')
        page.evaluate("document.getElementById('benchRoute').style.filter = 'grayscale(1)'")
        expect(bench.locator('[data-task]')).to_have_count(3)
        assert bench.locator('[data-task]').evaluate_all('(els) => els.map(e => e.dataset.lane)') == ['done', 'doing', 'next']
        shapes = bench.locator('[data-verification] svg').evaluate_all('(els) => els.map(e => e.innerHTML)')
        assert len(set(shapes)) == 3
        assert bench.locator('[data-state="met"]').first.evaluate('(e) => getComputedStyle(e).borderStyle') != bench.locator('[data-state="unmet"]').first.evaluate('(e) => getComputedStyle(e).borderStyle')
        expect(bench.locator('[data-agent]')).to_have_count(5)
        assert bench.locator('[data-agent]').first.evaluate('(e) => e.style.getPropertyValue("--hc")')
        assert len(set(bench.locator('[data-agent] .glyph svg').evaluate_all('(els) => els.map(e => e.innerHTML)'))) == 5
        expect(bench.locator('[data-action="unknown"]')).to_have_count(1)
        expect(bench.locator('[data-desk] [data-lantern]')).to_have_count(1)
        bench.locator('[data-tray] summary').click()
        expect(bench.locator('[data-tray]')).to_contain_text('Evidence report')
        bench.locator('[data-tray] summary').click()
        expect(bench.locator('[data-summary]')).to_be_hidden()
        bench.locator('[data-briefing]').click()
        expect(bench.locator('[data-summary]')).to_contain_text('Find suppliers')
        bench.locator('[data-briefing]').click()
        assert page.evaluate("fleetDeck.textBudget(document.getElementById('benchRoute'))") <= 35
        if redact:
            assert page.evaluate("fleetDeck.textBudget(document.getElementById('benchRoute'))") == 0
            assert bench.locator('[data-verification] svg').first.evaluate('(e) => getComputedStyle(e).stroke') != 'rgba(0, 0, 0, 0)'
        doc['tasks'][1].update(lane='done', condition='complete')
        bench.locator('[data-tray] summary').click()
        doc['agents'].append({**doc['agents'][0], 'run': 'six'})
        doc['attention'] = []
        page.evaluate('doc => fleetDeck.apply(doc)', finish_jobs(base_url, {}))
        expect(bench.locator('[data-task="doing"]')).to_have_attribute('data-flipped', 'true')
        expect(bench.locator('[data-agent-group]')).to_have_attribute('data-count', '6')
        expect(bench.locator('[data-agent]')).to_have_count(0)
        expect(bench.locator('[data-lantern]')).to_have_count(0)
        expect(bench.locator('[data-tray]')).to_have_attribute('open', '')
        page.evaluate('doc => fleetDeck.apply(doc)', finish_jobs(base_url, {}))
        expect(bench.locator('[data-task="doing"]')).to_have_attribute('data-flipped', 'false')
        for actions in [['web', 'plan', 'delegate', 'type', 'doc'], ['ship', 'review', 'build', 'think', 'search']]:
            doc['agents'] = [{**doc['agents'][0], 'run': str(i), 'action_glyph': action}
                             for i, action in enumerate(actions)]
            page.evaluate('doc => fleetDeck.apply(doc)', finish_jobs(base_url, {}))
            expect(bench.locator(f'[data-action="{actions[0]}"]')).to_have_count(1)
            expect(bench.locator('[data-agent]')).to_have_count(5)
        doc['summary'] = None
        doc['progress'] = {'complete': None, 'total': None}
        page.evaluate('doc => fleetDeck.apply(doc)', finish_jobs(base_url, {}))
        expect(bench).to_contain_text('Progress unknown')
        bench.locator('[data-briefing]').click()
        expect(bench.locator('[data-summary]')).to_have_text('Summary unknown')
    finally:
        page.unroute('**/api/bench?*', respond)
        page.evaluate('fleetDeck.enterFloor(null)')
        if redact:
            page.goto(base_url + '/')
            page.wait_for_function('window.fleetDeck')


def test_text_budget_detects_overflow(deck: Deck) -> None:
    assert deck.page.evaluate("""async () => {
        const { assertTextBudget } = await import('/js/text-budget.js');
        const el = document.body.appendChild(document.createElement('div'));
        el.textContent = 'one two three';
        try {
            assertTextBudget(el, 3);
            try { assertTextBudget(el, 2); } catch (e) { return /3 > 2/.test(e.message); }
            return false;
        } finally { el.remove(); }
    }""")


def test_redact_has_no_visible_text(browser: Browser, base_url: str) -> None:
    page = browser.new_page()
    try:
        page.goto(base_url + '/?redact')
        page.wait_for_function('window.fleetDeck')
        page.evaluate("fleetDeck.enterFloor('p-5e1f0a01')")
        page.locator('[data-epic]').first.click()
        page.locator('[data-slice]').first.click()
        assert page.evaluate("fleetDeck.textBudget(document.body)") == 0
        assert page.evaluate("""() => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                const node = walker.currentNode;
                if (!node.textContent.trim() || !node.parentElement.checkVisibility()) continue;
                if (getComputedStyle(node.parentElement).color !== 'rgba(0, 0, 0, 0)') return false;
            }
            return true;
        }""")
    finally:
        page.close()


def test_every_unfinished_job_and_session_is_an_agent(deck: Deck, fixture_data: dict[str, Any]) -> None:
    expected = on_the_floor(fixture_data)
    agents = deck.page.evaluate("fleetDeck.agents()")
    assert {agent["key"] for agent in agents} == expected
    assert sum(agent["kind"] == "session" for agent in agents) == 2
    expect(deck.page.locator("#tags .tag")).to_have_count(len(expected))
    expect(deck.page.locator("#tags .tag.sess")).to_have_count(2)
    expect(deck.page.locator("#legendBody .crew.off")).to_have_count(1)
    assert deck.errors == []


def test_finished_jobs_are_off_the_deck_until_the_chip_shows_them(deck: Deck, fixture_data: dict[str, Any]) -> None:
    page = deck.page
    finished = {f"{host['name']}:{job['id']}" for host in fixture_data["hosts"]
                for job in host["jobs"] if job["status"] in FINISHED}
    assert finished == {"worker:d4f7a2"}
    assert not finished & {agent["key"] for agent in page.evaluate("fleetDeck.agents()")}
    expect(page.locator("#tags .tag", has_text="d4f7a2")).to_have_count(0)
    chip = page.locator("#toggleFinished")
    expect(chip).to_have_text("1 finished · show")

    chip.dispatch_event("click")
    page.wait_for_function("fleetDeck.agents().some(agent => agent.key === 'worker:d4f7a2')")
    expect(page.locator("#tags .tag", has_text="d4f7a2")).to_have_count(1)
    expect(chip).to_have_text("hide finished")

    chip.dispatch_event("click")
    page.wait_for_function("!fleetDeck.agents().some(agent => agent.key === 'worker:d4f7a2')")
    expect(page.locator("#tags .tag", has_text="d4f7a2")).to_have_count(0)
    expect(chip).to_have_text("1 finished · show")
    assert deck.errors == []


def finish_jobs(base_url: str, statuses: dict[str, str]) -> dict[str, Any]:
    """The server's state document with some jobs finished, as the next state update would bring it."""
    with urlopen(base_url + "/api/state", timeout=5) as response:
        doc = json.load(response)
    for host in doc["hosts"]:
        for job in host["jobs"]:
            job["status"] = statuses.get(f"{host['name']}:{job['id']}", job["status"])
    return doc


@pytest.mark.parametrize("changed_deck", ["no-preference", "reduce"], indirect=True)
def test_a_job_that_finishes_walks_out(changed_deck: Deck, base_url: str) -> None:
    page = changed_deck.page
    leaving = {"worker:c90e11": "done", "home:b7d042": "cancelled"}
    page.evaluate("doc => fleetDeck.apply(doc)", finish_jobs(base_url, leaving))
    gone = f"!fleetDeck.agents().some(agent => {json.dumps(list(leaving))}.includes(agent.key))"
    if page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"):
        assert page.evaluate(gone)                                          # removed at once
    else:
        agents = {agent["key"]: agent for agent in page.evaluate("fleetDeck.agents()")}
        assert all(agents[key]["leaving"] for key in leaving)               # a completion moment, then the door
        assert not agents["home:a1c3e9"]["leaving"]
        page.evaluate("fleetDeck.advanceTime(0)")
        assert page.evaluate(gone) is False
        advance_until(page, gone)
    expect(page.locator("#tags .tag", has_text="c90e11")).to_have_count(0)
    expect(page.locator("#toggleFinished")).to_have_text("3 finished · show")
    assert changed_deck.errors == []


def test_idle_sessions_rest_without_a_bubble(deck: Deck) -> None:
    page = deck.page
    advance_until(page, f"fleetDeck.agents().filter(agent => [{json.dumps(ASKING)}, {json.dumps(REVIEWING)}]"
                           ".includes(agent.key) && agent.clip === 'Sitting').length === 2")
    for room, session, job in [("restoke", "Why does the st", "a1c3e9"), ("agent-fleet", "review the unstaged", "f20a6d")]:
        rooms_on_screen(page, room)
        expect(page.locator("#tags .tag", has_text=job).locator(".bubble")).to_be_visible()
        expect(page.locator("#tags .tag", has_text=session).locator(".bubble")).to_be_hidden()
    page.evaluate("fleetDeck.lookAtRoom(null)")
    assert deck.errors == []


def session_state(base_url: str, seconds_later: int, drop_decisions: bool = False,
                  working: str | None = None) -> dict[str, Any]:
    """The server's state document, optionally without its decision items or with one session back at work."""
    with urlopen(base_url + "/api/state", timeout=5) as response:
        doc = json.load(response)
    if drop_decisions:
        doc["attention"] = [item for item in doc["attention"] if item["kind"] != "decision"]
        from fleet.projections.attention import attention_display
        doc["attention_display"] = attention_display(doc["attention"], doc["building"], doc["projects"])
    for host in doc["hosts"]:
        for session in host["sessions"]:
            if f"{host['name']}:{session['id']}" == working:
                session.update(status="working", updated_at=doc["time"] + seconds_later)
    return doc


def test_sessions_idle_for_half_an_hour_leave_unless_a_decision_waits(changed_deck: Deck, base_url: str) -> None:
    page = changed_deck.page
    live = page.locator("#stats .chip.sess")
    expect(live).to_have_text("2 live · 2 waiting")

    later = 40 * 60                                                     # both now idle for over half an hour
    page.evaluate(f"advanceClock({later})")
    page.evaluate("fleetDeck.advanceTime(0)")
    page.wait_for_function(f"!fleetDeck.agents().some(agent => agent.key === {json.dumps(REVIEWING)})")
    assert ASKING in {agent["key"] for agent in page.evaluate("fleetDeck.agents()")}
    expect(live).to_have_text("2 live · 2 waiting")

    page.evaluate("doc => fleetDeck.apply(doc)", session_state(base_url, later, drop_decisions=True))
    assert not {ASKING, REVIEWING} & {agent["key"] for agent in page.evaluate("fleetDeck.agents()")}
    expect(live).to_have_text("2 live · 2 waiting")

    page.evaluate("doc => fleetDeck.apply(doc)", session_state(base_url, later, drop_decisions=True, working=REVIEWING))
    assert REVIEWING in {agent["key"] for agent in page.evaluate("fleetDeck.agents()")}
    expect(live).to_have_text("2 live · 1 waiting")
    assert changed_deck.errors == []


def test_bubbles_show_action_glyphs_and_the_words_stay_a_click_away(deck: Deck) -> None:
    phrase = deck.page.evaluate("""async () => {
      const { mumble } = await import('/js/activity.js');
      return mumble({kind: 'tool', name: 'shell', summary: 'git status', activity_class: 'test'});
    }""")
    assert 'tests' in phrase
    page = deck.page
    expected = {"a1c3e9": "test", "b7d042": "edit", "Why does the st": "wait", "f20a6d": "edit", "c90e11": "think"}
    for agent, action in expected.items():
        bubble = page.locator("#tags .tag", has_text=agent).locator(".bubble")
        expect(bubble).to_have_attribute("data-action", action)
        expect(bubble.locator(f'.glyph[data-action="{action}"][role="img"]')).to_have_count(1)
        assert bubble.text_content().strip() == ""                       # no sentence in the bubble
    running = page.locator("#tags .tag", has_text="a1c3e9")
    expect(running.locator(".bubble")).to_have_attribute("title", re.compile("tests"))
    running.dispatch_event("click")
    expect(page.locator("#panel")).to_have_class("open")
    expect(page.locator("#panelBody .evs")).to_contain_text("pnpm vitest run suppliers")
    page.locator("#panel #close").click()
    expect(page.locator("#panel")).not_to_have_class("open")
    expect(page.locator("#feed")).to_contain_text("pnpm vitest run suppliers")   # and the deck log keeps it
    assert deck.errors == []


def test_a_jobs_workarea_shows_its_plan_desk_and_tray(deck: Deck, fixture_data: dict[str, Any]) -> None:
    page = deck.page
    page.locator("#tags .tag", has_text="a1c3e9").dispatch_event("click")
    page.locator("#panelTabs [data-workarea]").click()
    workarea = page.locator("#workarea")
    expect(workarea).to_be_visible()
    expect(workarea.locator(".wa-head h2")).to_have_text("restoke")

    # tiles match each bench's step statuses, marked without relying on colour
    jobs = {f"{host['name']}:{job['id']}": job for host in fixture_data["hosts"] for job in host["jobs"]}
    benches = workarea.locator("[data-bench]")
    expect(benches).to_have_count(4)
    marks = {"done": "✓", "running": "●", "failed": "✗", "pending": ""}
    for key in benches.evaluate_all("benches => benches.map(b => b.dataset.bench)"):
        tiles = workarea.locator(f'[data-bench="{key}"] [data-tile]')
        steps = jobs[key]["steps"]
        assert tiles.evaluate_all("tiles => tiles.map(t => t.dataset.status)") == [step["status"] for step in steps]
        assert [text.strip() for text in tiles.locator(".mk").all_inner_texts()] == [marks[step["status"]] for step in steps]
        done = sum(step["status"] == "done" for step in steps)
        expect(workarea.locator(f'[data-bench="{key}"] .criteria')).to_have_attribute("aria-label", f"{done} of {len(steps)} steps done")
    expect(workarea.locator('[data-bench="home:b7d042"] [data-mark="glow"]')).to_have_count(1)

    # the lantern hangs over the question desk, with the room's two items
    lantern = workarea.locator(".wa-lantern")
    expect(lantern).to_have_attribute("data-count", "2")
    hang, desk = lantern.bounding_box(), workarea.locator(".desk-top").bounding_box()
    assert desk["x"] <= hang["x"] + hang["width"] / 2 <= desk["x"] + desk["width"]
    assert hang["y"] + hang["height"] <= desk["y"] + 2
    expect(workarea.locator(".wa-steps path")).to_have_count(4)   # every bench here was used in the last hour

    # the tray opens the job's step report in the reader; Esc closes the reader, then the workarea
    expect(workarea.locator('[data-tray="home:a1c3e9"]')).to_be_disabled()
    workarea.locator('[data-tray="worker:d4f7a2"]').click()
    expect(page.locator("#reader")).to_be_visible()
    expect(page.locator("#rdTitle")).to_have_text("Step 2: Draft the article")

    # ← → and the buttons step through the job's documents in the panel's order (newest produced first)
    expect(page.locator("#rdPos")).to_have_text("1 / 2")
    expect(page.locator("#rdPrev")).to_be_disabled()
    page.keyboard.press("ArrowRight")
    expect(page.locator("#rdPos")).to_have_text("2 / 2")
    expect(page.locator("#rdTitle")).to_have_text("par-by-weekday.md")
    page.locator("#rdPrev").click()
    expect(page.locator("#rdTitle")).to_have_text("Step 2: Draft the article")
    page.keyboard.press("Escape")
    expect(page.locator("#reader")).to_be_hidden()
    expect(workarea).to_be_visible()

    # a plan tile opens its step's report; a step with nothing to read is disabled
    workarea.locator('[data-bench="worker:d4f7a2"] [data-step="1"]').click()
    expect(page.locator("#rdTitle")).to_have_text("Step 2: Draft the article")
    page.keyboard.press("Escape")
    expect(workarea.locator('[data-bench="home:a1c3e9"] [data-step="0"]')).to_be_disabled()

    page.keyboard.press("Escape")
    expect(workarea).to_be_hidden()
    expect(page.locator("#panel")).to_have_class(re.compile("open"))   # one level at a time

    # a bench's title opens that job's panel, and the entrance leaves
    page.locator("#panelTabs [data-workarea]").click()
    expect(workarea.locator('[data-bench="worker:d4f7a2"] [data-job]')).to_be_disabled()   # finished: not on the deck
    workarea.locator('[data-bench="home:b7d042"] [data-job]').click()
    expect(workarea).to_be_hidden()
    expect(page.locator("#panelHead h2")).to_have_text(jobs["home:b7d042"]["description"])
    page.locator("#panelTabs [data-workarea]").click()
    workarea.locator("[data-entrance]").click()
    expect(workarea).to_be_hidden()
    page.locator("#panel #close").click()
    assert deck.errors == []


def test_document_reader_opens_from_a_failed_jobs_panel(deck: Deck) -> None:
    page = deck.page
    page.locator('.lantern[data-room="restoke"]').dispatch_event("click")
    page.locator('#attnPanel [data-owner="home:e1b5c8"]').click()
    page.locator("#attnPanel [data-close]").click()
    expect(page.locator("#panel")).to_have_class("open")
    expect(page.locator("#panelHead h2")).to_have_text("Upgrade Django to 5.2")
    page.locator('#panelBody [data-tab="summary"] [data-doc="report-0"]').click()   # the finished step's report
    expect(page.locator("#reader")).to_be_visible()
    expect(page.locator("#rdBody h1")).to_have_text("Django 5.2 upgrade blocked")
    expect(page.locator("#rdBody")).not_to_contain_text("FLEET_STATUS")

    expect(page.locator("#rdStep")).to_be_hidden()   # its only document: nothing to step to
    page.keyboard.press("Escape")
    expect(page.locator("#reader")).to_be_hidden()
    page.locator("#panel #close").click()
    expect(page.locator("#panel")).not_to_have_class("open")
    assert deck.errors == []


def test_demo_mode_fills_the_deck_without_errors(browser: Browser, base_url: str) -> None:
    context = browser.new_context(viewport=VIEWPORTS["desktop"], reduced_motion="reduce")
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda message: message.type == "error" and errors.append(message.text))
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/?demo")
    page.wait_for_function("window.fleetDeck && fleetDeck.agents().length > 0")
    expect(page.locator("#live")).to_contain_text("demo data")
    assert len(page.evaluate("fleetDeck.rooms()")) > 1
    assert not any(agent["status"] in BLOCKED for agent in page.evaluate("fleetDeck.agents()"))
    parser = rooms_by_name(page)["demo-parser"]                              # in the background, its parse running
    assert (parser["focus"], parser["lit"]) == ("background", True)
    assert crew(page, "demo-parser") == set()
    advance_until(page, "fleetDeck.crowds().some(crowd => crowd.room === 'demo-docs' && crowd.count === 6)")
    expect(page.locator('.lantern[data-kind="blocker"]')).to_have_count(2)   # the failed job's and the stalled one's
    assert any(room["attention"] and room["attention"]["count"] > 1 for room in page.evaluate("fleetDeck.rooms()"))
    expect(page.locator('.lantern[data-count="2"] b')).to_have_text("2")
    page.locator('.lantern[data-room="demo-docs"]').dispatch_event("click")
    page.locator("#attnPanel [data-owner]").dispatch_event("click")   # the demo pushes a new state every second
    expect(page.locator("#panelHead h2")).to_have_text("Refresh the onboarding guide screenshots")
    page.locator("#attnPanel [data-close]").dispatch_event("click")
    page.locator("#tags .tag").first.dispatch_event("click")
    expect(page.locator("#panel")).to_have_class("open")
    context.close()
    assert errors == []


def test_demo_androids_finish_and_walk_out(browser: Browser, base_url: str) -> None:
    context = browser.new_context(viewport=VIEWPORTS["desktop"])
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/?demo")
    page.wait_for_function("window.fleetDeck && fleetDeck.agents().length > 0")
    assert not any(agent["status"] in FINISHED for agent in page.evaluate("fleetDeck.agents()"))
    leaving = advance_until(page, "fleetDeck.agents().find(agent => agent.leaving)")
    assert leaving["status"] == "done"
    advance_until(page, f"!fleetDeck.agents().some(agent => agent.key === '{leaving['key']}')")
    context.close()
    assert errors == []


def test_demo_session_idle_for_an_hour_comes_back_to_work(browser: Browser, base_url: str) -> None:
    context = browser.new_context(viewport=VIEWPORTS["desktop"], reduced_motion="reduce")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/?demo")
    page.wait_for_function("window.fleetDeck && fleetDeck.agents().length > 0")
    sessions = page.evaluate("fleetDeck.agents().filter(agent => agent.kind === 'session')")
    assert {agent["status"] for agent in sessions} == {"working", "idle"}
    # the header also counts the dormant session and the one working in the background room
    expect(page.locator("#stats .chip.sess")).to_have_text(f"{len(sessions) + 2} live · 2 waiting")
    advance_until(page, f"fleetDeck.agents().filter(agent => agent.kind === 'session').length === {len(sessions) + 1}")
    expect(page.locator("#stats .chip.sess")).to_have_text(f"{len(sessions) + 2} live · 1 waiting")
    context.close()
    assert errors == []


def test_library_lists_and_opens_documents(deck: Deck) -> None:
    page = deck.page
    page.locator("#libraryOpen").click()
    page.locator('[data-lib-view="all"]').click()
    expect(page.locator("#libList .lib-doc[data-project]")).to_have_count(3)
    # a library key that is a linked label joins its registered project's group
    expect(page.locator("#libList .lib-group h3")).to_have_text(["agent-fleet", "Restoke"])
    page.locator('#libList .tree-folder[data-folder="restoke:docs/"] > summary').click()   # folders start folded
    page.locator('#libList .lib-doc[data-id="docs/suppliers-v2.md"]').click()
    expect(page.locator("#reader")).to_be_visible()
    expect(page.locator("#rdBody h1")).to_have_text("Suppliers V2")
    page.keyboard.press("Escape")
    expect(page.locator("#reader")).to_be_hidden()
    page.locator('[data-lib-view="overview"]').click()   # leave the shared page on the default view
    page.locator(".lib-head [data-lib-close]").click()
    expect(page.locator("#libraryPane")).to_be_hidden()
    assert deck.errors == []


def test_bubbles_appear_only_in_a_room_you_zoom_into(deck: Deck) -> None:
    page = deck.page
    agents = page.evaluate("fleetDeck.agents()")
    room = next(agent["room"] for agent in agents if agent["key"].endswith(":a1c3e9"))
    none_speak = "[...document.querySelectorAll('#tags .tag')].every(tag => getComputedStyle(tag.firstChild).display === 'none')"
    page.evaluate("fleetDeck.lookAtRoom(null)")                          # the whole deck: no bubbles
    advance_until(page, none_speak)
    on_screen = rooms_on_screen(page, room)                              # zooms into the room
    far_away = [agent["key"].split(":")[1] for agent in agents if agent["room"] not in on_screen]
    expect(page.locator("#tags .tag", has_text="a1c3e9").locator(".bubble")).to_be_visible()
    for job in far_away:
        expect(page.locator("#tags .tag", has_text=job).locator(".bubble")).to_be_hidden()
    page.evaluate("fleetDeck.lookAtRoom(null)")
    advance_until(page, none_speak)
    assert deck.errors == []


def crowded_state(base_url: str, copies: int) -> dict[str, Any]:
    """The server's state document with worker:f20a6d, editing at the agent-fleet workbench, joined there by copies of
    itself."""
    doc = finish_jobs(base_url, {})
    worker = next(host for host in doc["hosts"] if host["name"] == "worker")
    job = next(job for job in worker["jobs"] if job["id"] == "f20a6d")
    worker["jobs"] += [{**job, "id": f"f20a6d-{n}"} for n in range(1, copies + 1)]
    return doc


def crowded_deck(deck: Deck, base_url: str, copies: int) -> Page:
    """Zoom into agent-fleet with that many more androids at the workbench."""
    page = deck.page
    page.evaluate("doc => fleetDeck.apply(doc)", crowded_state(base_url, copies))
    page.evaluate("fleetDeck.lookAtRoom('agent-fleet', 60)")
    return page


def at_the_workbench(page: Page) -> list[dict[str, Any]]:
    return [agent for agent in page.evaluate("fleetDeck.agents()")
            if agent["room"] == "agent-fleet" and agent["station"] == "workbench"]


def click_canvas(page: Page, x: int, y: int) -> None:
    """A click on the 3D view itself, whatever overlay is above that point."""
    page.evaluate("""([x, y]) => {
      const opts = { clientX: x, clientY: y, pointerId: 97, pointerType: 'mouse', bubbles: true };
      for (const type of ['pointerdown', 'pointerup']) document.getElementById('world').dispatchEvent(new PointerEvent(type, opts));
    }""", [x, y])


def test_six_androids_at_one_station_gather_into_a_group_figure(changed_deck: Deck, base_url: str) -> None:
    page = crowded_deck(changed_deck, base_url, 5)
    advance_until(page, "fleetDeck.crowds().length === 1")
    assert len(at_the_workbench(page)) == 6
    assert page.evaluate("fleetDeck.crowds()") == [
        {"room": "agent-fleet", "station": "workbench", "count": 6, "fanned": False}]
    assert all(agent["gathered"] for agent in at_the_workbench(page))
    badge = page.locator("#tags .crowd")
    expect(badge).to_have_count(1)
    expect(badge).to_have_text("6")
    expect(page.locator("#tags .tag:visible", has_text="f20a6d")).to_have_count(0)
    assert not any(agent["gathered"] for agent in page.evaluate("fleetDeck.agents()")
                   if agent["room"] == "agent-fleet" and agent["station"] != "workbench")
    assert changed_deck.errors == []


def test_clicking_the_group_fans_it_out_and_clicking_away_gathers_it(changed_deck: Deck, base_url: str) -> None:
    page = crowded_deck(changed_deck, base_url, 5)
    advance_until(page, "fleetDeck.crowds().length === 1")
    badge = page.locator("#tags .crowd")
    badge.click()
    page.wait_for_function("fleetDeck.crowds()[0].fanned")
    expect(badge).to_be_hidden()
    expect(page.locator("#tags .tag:visible", has_text="f20a6d")).to_have_count(6)
    assert not any(agent["gathered"] for agent in at_the_workbench(page))

    click_canvas(page, 5, page.viewport_size["height"] - 5)             # empty floor between the rooms
    page.wait_for_function("!fleetDeck.crowds()[0].fanned")
    expect(badge).to_be_visible()
    expect(page.locator("#tags .tag:visible", has_text="f20a6d")).to_have_count(0)

    badge.click()                                                       # fanned out, one opened: it stands alone
    page.locator("#tags .tag", has_text="f20a6d-3").click()
    expect(page.locator("#panel")).to_have_class(re.compile("open"))
    advance_until(page, "fleetDeck.crowds()[0].count === 5")
    assert page.evaluate("fleetDeck.crowds()[0].fanned")
    page.locator("#panel #close").click()                               # closing the panel gathers them again
    advance_until(page, "!fleetDeck.crowds()[0].fanned && fleetDeck.crowds()[0].count === 6")
    expect(page.locator("#tags .tag:visible", has_text="f20a6d")).to_have_count(0)
    assert changed_deck.errors == []


def test_five_androids_at_one_station_stand_on_their_own(changed_deck: Deck, base_url: str) -> None:
    page = crowded_deck(changed_deck, base_url, 4)
    advance_until(page, "fleetDeck.agents().filter(agent => agent.room === 'agent-fleet' && agent.station === 'workbench').length === 5")
    assert page.evaluate("fleetDeck.crowds()") == []
    assert not any(agent["gathered"] for agent in at_the_workbench(page))
    expect(page.locator("#tags .crowd")).to_have_count(0)
    expect(page.locator("#tags .tag:visible", has_text="f20a6d")).to_have_count(5)
    assert changed_deck.errors == []


def rooms_on_screen(page: Page, zoomed: str) -> set[str]:
    """Rooms whose middle would be on screen with the view zoomed into another room."""
    page.evaluate(f"fleetDeck.lookAtRoom({json.dumps(zoomed)}, 60)")
    size = page.viewport_size
    return {name for name, room in rooms_by_name(page).items()
            if 0 < room["screen"]["x"] < size["width"] and 0 < room["screen"]["y"] < size["height"]}


def rooms_by_name(page: Page) -> dict[str, dict[str, Any]]:
    return {room["name"]: room for room in page.evaluate("fleetDeck.rooms()")}


def tag_is_calm(page: Page, job_id: str) -> bool:
    """A calm tag has no speech bubble showing."""
    tag = page.locator("#tags .tag", has_text=job_id)
    return tag.evaluate("tag => tag.classList.contains('calm') && getComputedStyle(tag.firstChild).display === 'none'")


def focus_on_server(base_url: str, room: str) -> set[str]:
    """The focus the server resolves for every job and session in a room."""
    with urlopen(base_url + "/api/state", timeout=5) as response:
        hosts = json.load(response)["hosts"]
    return {item["focus"] for host in hosts for item in host["jobs"] + host["sessions"] if item["project"] == room}


def settled_focus_on_server(base_url: str, room: str, focus: str) -> set[str]:
    """The room's focus once the switch's POST has landed: the deck dims a room before the server confirms it."""
    deadline = time.monotonic() + 5
    while (found := focus_on_server(base_url, room)) != {focus} and time.monotonic() < deadline:
        time.sleep(0.05)
    return found


def room_colour(page: Page, room: str) -> tuple[float, float, float]:
    """Mean brightness, saturation and warmth (red over blue) of the rendered floor around a room's centre, with
    overlays hidden."""
    centre = rooms_by_name(page)[room]["screen"]
    viewport = page.viewport_size
    box = {"x": max(0, centre["x"] - 40), "y": max(0, centre["y"] - 30), "width": 80, "height": 60}
    assert box["x"] + 80 <= viewport["width"] and box["y"] + 60 <= viewport["height"]
    style = page.add_style_tag(content="#tags,#floorUi,header,.card,#zoom{visibility:hidden!important}")
    page.evaluate("fleetDeck.advanceTime(0)")
    image = Image.open(io.BytesIO(page.screenshot(clip=box))).convert("RGB")
    style.evaluate("tag => tag.remove()")
    _, saturation, value = ImageStat.Stat(image.convert("HSV")).mean
    red, _, blue = ImageStat.Stat(image).mean
    return value, saturation, red - blue


def wait_for_dim(page: Page, room: str, dim: int) -> None:
    page.wait_for_function(f"(fleetDeck.advanceTime(0.1), fleetDeck.rooms().find(room => room.name === '{room}').dim === {dim})")


def crew(page: Page, room: str) -> set[str]:
    """The androids in a room."""
    return {agent["key"] for agent in page.evaluate("fleetDeck.agents()") if agent["room"] == room}


def test_background_rooms_are_dim_with_no_crew(deck: Deck) -> None:
    page = deck.page
    wait_for_dim(page, "invoice-parser", 1)
    rooms = rooms_by_name(page)
    assert {name: (room["focus"], room["dim"], room["lit"]) for name, room in rooms.items()} == {
        "restoke": ("priority", 0, False), "invoice-parser": ("background", 1, False), "agent-fleet": ("priority", 0, False)}
    expect(page.locator('.focus-switch[data-room="invoice-parser"]')).to_have_attribute("data-focus", "background")
    expect(page.locator('.focus-switch[data-room="agent-fleet"]')).to_have_attribute("data-focus", "priority")
    assert crew(page, "invoice-parser") == set()
    expect(page.locator("#tags .tag", has_text="0a9e3b")).to_have_count(0)
    assert not tag_is_calm(page, "c90e11") and not tag_is_calm(page, "f20a6d")
    assert deck.errors == []


def test_a_background_room_is_lit_while_its_work_runs(changed_deck: Deck, base_url: str) -> None:
    page = changed_deck.page
    assert rooms_by_name(page)["invoice-parser"]["lit"] is False                 # a queued job is not running
    in_priority = finish_jobs(base_url, {})
    for host in in_priority["hosts"]:
        for item in host["jobs"] + host["sessions"]:
            item["focus"] = "priority"
    page.evaluate("doc => fleetDeck.apply(doc)", in_priority)
    wait_for_dim(page, "invoice-parser", 0)
    bright, vivid, _ = room_colour(page, "invoice-parser")
    page.evaluate("doc => fleetDeck.apply(doc)", finish_jobs(base_url, {}))
    wait_for_dim(page, "invoice-parser", 1)
    dim, grey, cool = room_colour(page, "invoice-parser")
    assert dim < bright * 0.75 and grey < vivid * 0.6                            # nothing runs: dim and grey
    page.evaluate("doc => fleetDeck.apply(doc)", finish_jobs(base_url, {"worker:0a9e3b": "running"}))
    rooms = rooms_by_name(page)
    assert rooms["invoice-parser"]["lit"] is True
    dim, _, warm = room_colour(page, "invoice-parser")
    assert dim < bright * 0.75 and warm > cool + 20                              # its job runs: dim, but lit warm
    assert rooms["invoice-parser"]["attention"] == {"kind": "blocker", "state": "open", "count": 1}
    assert crew(page, "invoice-parser") == set()
    expect(page.locator("#tags .tag", has_text="0a9e3b")).to_have_count(0)
    expect(page.locator("#stats .chip", has_text="working")).to_have_text("5 working")   # still counted
    page.evaluate("doc => fleetDeck.apply(doc)", finish_jobs(base_url, {}))
    assert rooms_by_name(page)["invoice-parser"]["lit"] is False
    assert changed_deck.errors == []


def test_the_switch_sends_the_crew_away_and_brings_it_back(deck: Deck, base_url: str) -> None:
    page = deck.page
    before = {name: (room["x"], room["y"], room["screen"]) for name, room in rooms_by_name(page).items()}
    bright, _, cool = room_colour(page, "restoke")
    crew_before = crew(page, "restoke")
    assert {"home:a1c3e9", "worker:c90e11", ASKING} <= crew_before
    switch = page.locator('.focus-switch[data-room="restoke"]')
    switch.locator('[data-set="background"]').dispatch_event("click")
    expect(switch).to_have_attribute("data-focus", "background")
    wait_for_dim(page, "restoke", 1)
    assert settled_focus_on_server(base_url, "restoke", "background") == {"background"}
    dim, _, warm = room_colour(page, "restoke")
    assert dim < bright * 0.75 and warm > cool + 20                            # its work runs: dim, but lit warm
    assert crew(page, "restoke") == set()
    expect(page.locator("#tags .tag", has_text="c90e11")).to_have_count(0)
    assert rooms_by_name(page)["restoke"]["lit"] is True
    lantern = page.locator('.lantern[data-room="restoke"]')                    # the lantern is unaffected
    expect(lantern).to_have_attribute("data-count", "2")
    lantern.dispatch_event("click")
    page.locator(f'#attnPanel [data-owner="{ASKING}"]').click()
    expect(page.locator("#panelHead .chip.sess")).to_contain_text("waiting for you")
    page.locator("#panel #close").click()
    page.keyboard.press("Escape")
    assert {name: (room["x"], room["y"], room["screen"]) for name, room in rooms_by_name(page).items()} == before

    switch.locator('[data-set="priority"]').dispatch_event("click")
    wait_for_dim(page, "restoke", 0)
    assert settled_focus_on_server(base_url, "restoke", "priority") == {"priority"}
    assert crew(page, "restoke") == crew_before
    assert rooms_by_name(page)["restoke"]["lit"] is False
    assert not tag_is_calm(page, "c90e11")
    assert {name: (room["x"], room["y"], room["screen"]) for name, room in rooms_by_name(page).items()} == before
    assert deck.errors == []


def test_focus_of_an_unregistered_room_survives_reload(deck: Deck, base_url: str, fixture_data: dict[str, Any]) -> None:
    page = deck.page
    page.locator('.focus-switch[data-room="agent-fleet"] [data-set="background"]').dispatch_event("click")
    wait_for_dim(page, "agent-fleet", 1)
    assert settled_focus_on_server(base_url, "agent-fleet", "background") == {"background"}
    page.reload()
    crew_of_agent_fleet = {"worker:f20a6d", REVIEWING}
    page.wait_for_function("window.fleetDeck && (fleetDeck.advanceTime(0), fleetDeck.agents().length === "
                           f"{len(on_the_floor(fixture_data) - crew_of_agent_fleet)})")
    wait_for_dim(page, "agent-fleet", 1)
    expect(page.locator('.focus-switch[data-room="agent-fleet"]')).to_have_attribute("data-focus", "background")
    assert crew(page, "agent-fleet") == set()
    page.locator('.focus-switch[data-room="agent-fleet"] [data-set="priority"]').dispatch_event("click")
    wait_for_dim(page, "agent-fleet", 0)
    assert settled_focus_on_server(base_url, "agent-fleet", "priority") == {"priority"}
    assert crew(page, "agent-fleet") == crew_of_agent_fleet
    assert deck.errors == []


def test_the_deck_log_leaves_every_focus_switch_clear(deck: Deck) -> None:
    page = deck.page
    if page.viewport_size["width"] < 760:
        pytest.skip("on phones the log starts closed and the column scrolls under it")
    log = page.locator("#feed").bounding_box()
    switches = page.locator(".focus-switch")
    expect(switches).to_have_count(3)
    for switch in switches.all():
        box = switch.bounding_box()
        overlaps = (box["x"] < log["x"] + log["width"] and log["x"] < box["x"] + box["width"]
                    and box["y"] < log["y"] + log["height"] and log["y"] < box["y"] + box["height"])
        assert not overlaps, f"{switch.get_attribute('data-room')}'s switch is under the deck log"
    assert deck.errors == []


def attention_on_server(base_url: str) -> dict[str, str]:
    with urlopen(base_url + "/api/state", timeout=5) as response:
        return {item["owner"]["key"]: item["state"] for item in json.load(response)["attention"]}


@pytest.mark.parametrize("answer", ["Scenic", "3"])
def test_decision_reader_choices_and_submission(changed_deck: Deck, answer: str, base_url: str) -> None:
    from test_web_attention import Deck as ServerDeck
    from fleet.composition import open_decisions
    from fleet.projections.attention import attention_display

    server = ServerDeck()
    page = changed_deck.page
    question = server.state.attention.raise_item(project="p", kind="decision", owner="user",
        source="manual", source_reference="reader", headline="Which route?",
        context_reference="Review the route", actor="author", options=("Direct", "Scenic"))
    other = server.state.attention.raise_item(project="p", kind="decision", owner="user",
        source="manual", source_reference="other", headline="When?",
        context_reference="Schedule", actor="author")
    def proxy(route):
        response = route.fetch(url=server.url + "/api/" + route.request.url.split("/api/", 1)[1],
                               headers={"Content-Type": "application/json"})
        route.fulfill(response=response)
    page.route("**/api/decision**", proxy)
    try:
        item = server.state.document()["attention"][0]
        with urlopen(base_url + "/api/state", timeout=5) as response:
            document = json.load(response)
        item["project"] = "restoke"
        document["attention"].append(item)
        document["attention_display"] = attention_display(document["attention"], document["building"], document["projects"])
        page.evaluate("doc => fleetDeck.apply(doc)", document)
        before = page.evaluate("fleetDeck.rooms()")
        sequence = server.state.store.latest_sequence()
        page.locator('.lantern[data-room="restoke"]').dispatch_event("click")
        row = page.locator(f'#attnPanel .attn-item[data-id="{question.id}"]')
        expect(row.get_by_role("button", name="Open context", exact=True)).to_be_visible()
        row.get_by_role("button", name="Answer question", exact=True).click()
        expect(page.locator('#reader')).to_be_visible()
        expect(page.locator('#rdBody')).to_contain_text("Review the route")
        expect(page.get_by_role("radio", name="Scenic", exact=True)).to_be_visible()
        assert page.evaluate("fleetDeck.rooms()") == before
        assert server.state.store.latest_sequence() == sequence
        if answer == "Scenic":
            page.get_by_role("radio", name="Scenic", exact=True).check()
        else:
            page.get_by_label("Your answer", exact=True).fill(answer)
        page.get_by_role("button", name="Submit answer", exact=True).click()
        if answer == "Scenic":
            expect(page.locator('#rdBody')).to_contain_text("Answer recorded")
            decision, = open_decisions(server.state.store).list()
            assert (decision.actor, decision.answer) == ("user", "Scenic")
            assert server.state.attention.get(question.id).state == "resolved"
            assert server.state.attention.get(other.id).state == "open"
            updated = next(row for row in server.state.document()["attention"] if row["id"] == question.id)
            updated["project"] = "restoke"
            document["attention"] = [updated if row["id"] == question.id else row for row in document["attention"]]
            document["attention_display"] = attention_display(document["attention"], document["building"], document["projects"])
            page.evaluate("doc => fleetDeck.apply(doc)", document)
            assert rooms_by_name(page)["restoke"]["attention"]["count"] == 2
        else:
            expect(page.locator('#rdBody [role="alert"]')).to_contain_text("option number is out of range")
            assert server.state.attention.get(question.id).state == "open"
            assert server.state.store.latest_sequence() == sequence
            expect(page.get_by_label("Your answer", exact=True)).to_have_value(answer)
            assert page.evaluate("fleetDeck.rooms()") == before
    finally:
        page.unroute("**/api/decision**", proxy)
        server.close()


def shoot(request: pytest.FixtureRequest, page: Page, name: str) -> None:
    if request.config.getoption("--shots"):
        page.screenshot(path=f"{request.config.getoption('--shots')}/{name}.png")


@pytest.fixture
def refusing_server(changed_deck: Deck, base_url: str, tmp_path, monkeypatch):
    """A live deck server holding one job step's refused requests, standing in for the fixture's API where it acts."""
    from test_web_attention import Deck as ServerDeck, HOSTS
    from test_web_refusals import REQUESTS, refusal
    from fleet.projections.attention import attention_display
    from fleet.web.server import apply_message

    config = tmp_path / "hosts.json"
    config.write_text(json.dumps({"hosts": {"home": {}}}))
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    server = ServerDeck()
    apply_message(server.state, HOSTS[0], {"type": "hello"})
    for index, (tool, detail, description, rules) in enumerate(REQUESTS):
        apply_message(server.state, HOSTS[0], refusal(f"r{index}", tool, detail, description, rules, at=200 + index))
    page = changed_deck.page

    def proxy(route):
        response = route.fetch(url=server.url + "/api/" + route.request.url.split("/api/", 1)[1],
                               headers={"Content-Type": "application/json"})
        route.fulfill(response=response)
    for pattern in ("**/api/decision**", "**/api/attention/allow", "**/api/attention/dismiss"):
        page.route(pattern, proxy)
    [item] = server.state.document()["attention"]
    with urlopen(base_url + "/api/state", timeout=5) as response:
        document = json.load(response)
    item["project"] = "restoke"
    document["attention"].append(item)
    document["attention_display"] = attention_display(document["attention"], document["building"], document["projects"])
    page.evaluate("doc => fleetDeck.apply(doc)", document)
    try:
        yield server, item
    finally:
        for pattern in ("**/api/decision**", "**/api/attention/allow", "**/api/attention/dismiss"):
            page.unroute(pattern, proxy)
        server.close()


def test_refused_commands_are_one_item_answered_with_actions(changed_deck: Deck, refusing_server,
                                                             monkeypatch, request) -> None:
    from fleet import transport
    server, item = refusing_server
    page = changed_deck.page
    sent = []

    def call(host, arguments, stdin_text=None):
        sent.append(json.loads(stdin_text))
        return {"schema_version": 1, "key": arguments[arguments.index("--key") + 1], "status": "applied",
                "added": ["Bash"], "continuation": 2}
    monkeypatch.setattr(transport, "call", call)
    page.locator('.lantern[data-room="restoke"]').dispatch_event("click")
    row = page.locator(f'#attnPanel .attn-item[data-id="{item["id"]}"]')
    expect(row).to_contain_text("restoke step 2: 7 commands refused (Bash ×6, Read ×1)")
    expect(row.get_by_role("button", name="Answer question", exact=True)).to_have_count(0)
    shoot(request, page, "attention-popover-batch")
    row.get_by_role("button", name="Review refused commands", exact=True).click()
    body = page.locator("#rdBody")
    expect(body.locator(".refusals li")).to_have_count(7)
    expect(body).to_contain_text("git status --short")
    expect(body).to_contain_text("Check worktree state")
    expect(body).to_contain_text("Read(//etc/restoke.conf)")
    expect(body.get_by_label("Your answer")).to_have_count(0)
    for name in ("Allow these for this job", "Allow all Bash for this job", "Dismiss"):
        expect(body.get_by_role("button", name=name, exact=True)).to_be_enabled()
    shoot(request, page, "attention-reader-refusals")
    body.get_by_role("button", name="Allow all Bash for this job", exact=True).click()
    expect(body.locator(".refusal-done")).to_have_text(
        "allowed for job j1: Bash; step 2 continues as step 3; still not allowed: /etc/restoke.conf")
    assert sent == [["Bash"]]
    assert server.state.attention.get(item["id"]).state == "resolved"
    expect(body.get_by_role("button", name="Dismiss", exact=True)).to_be_disabled()
    page.keyboard.press("Escape")
    expect(page.locator("#reader")).to_be_hidden()
    expect(page.locator("#attnPanel")).to_be_visible()   # the reader took that Escape, not the list
    assert changed_deck.errors == []


def test_a_session_question_is_shown_with_where_to_answer_it(changed_deck: Deck, base_url: str, tmp_path,
                                                             monkeypatch, request) -> None:
    from test_web_attention import Deck as ServerDeck, HOSTS
    from test_web_session_questions import asked
    from fleet.projections.attention import attention_display
    from fleet.web.server import apply_message

    config = tmp_path / "hosts.json"
    config.write_text(json.dumps({"hosts": {"home": {}}}))
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    server = ServerDeck()
    apply_message(server.state, HOSTS[0], {"type": "hello"})
    apply_message(server.state, HOSTS[0], asked())
    page = changed_deck.page

    def proxy(route):
        route.fulfill(response=route.fetch(url=server.url + "/api/" + route.request.url.split("/api/", 1)[1]))
    page.route("**/api/decision**", proxy)
    try:
        [item] = server.state.document()["attention"]
        with urlopen(base_url + "/api/state", timeout=5) as response:
            document = json.load(response)
        document["attention"].append(item)
        document["attention_display"] = attention_display(document["attention"], document["building"], document["projects"])
        page.evaluate("doc => fleetDeck.apply(doc)", document)
        page.locator('.lantern[data-room="restoke"]').dispatch_event("click")
        row = page.locator(f'#attnPanel .attn-item[data-id="{item["id"]}"]')
        expect(row).to_contain_text("Probe run: The agent-friendliness probe needs a live site before it can…")
        expect(row.get_by_role("button", name="Answer question", exact=True)).to_have_count(0)
        shoot(request, page, "session-question-popover")
        row.get_by_role("button", name="Read the question", exact=True).click()
        body = page.locator("#rdBody")
        expect(body.get_by_role("note")).to_contain_text("Answer this in the session’s terminal on home")
        expect(body).to_contain_text("How should I run it?")
        expect(body.locator(".question-options li")).to_have_count(2)
        expect(body).to_contain_text("Run against staging now")
        expect(body).to_contain_text("/srv/restoke")
        expect(body.locator("textarea, input, form")).to_have_count(0)   # nothing to answer with here
        shoot(request, page, "session-question-reader")
        page.keyboard.press("Escape")
        assert changed_deck.errors == []
    finally:
        page.unroute("**/api/decision**", proxy)
        server.close()


def test_a_blocked_job_is_answered_from_the_deck(changed_deck: Deck, base_url: str, tmp_path,
                                                 monkeypatch, request) -> None:
    from test_web_attention import Deck as ServerDeck, HOSTS
    from test_answer_blocked import QUESTION, blocked
    from fleet import transport
    from fleet.projections.attention import attention_display
    from fleet.web.server import apply_message

    config = tmp_path / "hosts.json"
    config.write_text(json.dumps({"hosts": {"home": {}}}))
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    server = ServerDeck()
    apply_message(server.state, HOSTS[0], {"type": "hello"})
    server.report("home", jobs=[blocked()])
    sent = []

    def call(host, arguments, stdin_text=None):   # the worker, as fleetd answers a keyed add
        sent.append(json.loads(stdin_text))
        return {"schema_version": 1, "key": arguments[arguments.index("--key") + 1], "status": "applied",
                "answers": 1, "steps": [2]}
    monkeypatch.setattr(transport, "call", call)
    page = changed_deck.page

    def proxy(route):
        route.fulfill(response=route.fetch(url=server.url + "/api/" + route.request.url.split("/api/", 1)[1],
                                           headers={"Content-Type": "application/json"}))
    for pattern in ("**/api/decision**", "**/api/attention/answer"):
        page.route(pattern, proxy)
    try:
        [item] = server.state.document()["attention"]
        with urlopen(base_url + "/api/state", timeout=5) as response:
            document = json.load(response)
        document["attention"].append(item)
        document["attention_display"] = attention_display(document["attention"], document["building"], document["projects"])
        page.evaluate("doc => fleetDeck.apply(doc)", document)
        page.locator('.lantern[data-room="restoke"]').dispatch_event("click")
        row = page.locator(f'#attnPanel .attn-item[data-id="{item["id"]}"]')
        expect(row).to_contain_text("step 2 asks: May I exempt the existing")
        shoot(request, page, "blocked-job-popover")
        row.get_by_role("button", name="Answer", exact=True).click()
        body = page.locator("#rdBody")
        expect(body.locator(".blocked-message")).to_have_text(QUESTION)
        shoot(request, page, "blocked-job-reader")
        body.get_by_label("Your answer", exact=True).fill("Yes, exempt it and continue.")
        body.get_by_role("button", name="Send answer", exact=True).click()
        expect(body.locator('[role="status"]')).to_have_text("answered; step 2 continues as step 3")
        assert sent == [[{"prompt": "Yes, exempt it and continue.", "title": "Answer to step 2"}]]
        assert server.state.attention.get(item["id"]).state == "resolved"
        expect(body.get_by_role("button", name="Send answer", exact=True)).to_be_disabled()
        assert changed_deck.errors == []
    finally:
        for pattern in ("**/api/decision**", "**/api/attention/answer"):
            page.unroute(pattern, proxy)
        server.close()


def test_the_attention_list_closes_on_escape_or_a_click_away(changed_deck: Deck, base_url: str, request) -> None:
    from fleet.projections.attention import attention_display
    page = changed_deck.page
    with urlopen(base_url + "/api/state", timeout=5) as response:
        document = json.load(response)
    model = next(row for row in document["attention"] if row["project"] == "restoke")
    document["attention"] += [{**model, "id": f"extra{index}", "summary": f"Question {index} waiting on you",
                               "state": "open"} for index in range(12)]
    document["attention_display"] = attention_display(document["attention"], document["building"], document["projects"])
    page.evaluate("doc => fleetDeck.apply(doc)", document)
    lantern = page.locator('.lantern[data-room="restoke"]')
    panel, close = page.locator("#attnPanel"), page.locator("#attnPanel [data-close]")
    lantern.focus()
    lantern.press("Enter")
    expect(panel).to_be_visible()
    expect(close).to_be_focused()
    panel.evaluate("el => { el.scrollTop = el.scrollHeight; }")
    assert panel.evaluate("el => el.scrollTop") > 0, "the list should be long enough to scroll"
    box, frame = close.bounding_box(), panel.bounding_box()
    assert frame["y"] <= box["y"] and box["y"] + box["height"] <= frame["y"] + frame["height"], "close scrolled away"
    assert page.evaluate("""() => { const b = document.querySelector('#attnPanel [data-close]').getBoundingClientRect();
        return document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2)?.closest('[data-close]') !== null; }""")
    shoot(request, page, "attention-popover-open")
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(lantern).to_be_focused()
    expect(page.locator("#panel")).to_have_attribute("aria-hidden", "true")   # no job panel was opened or closed
    shoot(request, page, "attention-popover-after-escape")
    # A closed list follows the state too: no stale entries wait in it for the next opening.
    page.evaluate("doc => fleetDeck.apply(doc)", {**document, "attention": document["attention"][:-12],
        "attention_display": attention_display(document["attention"][:-12], document["building"], document["projects"])})
    expect(page.locator('#attnPanel .attn-item[data-id^="extra"]')).to_have_count(0)
    page.evaluate("doc => fleetDeck.apply(doc)", document)
    expect(page.locator('#attnPanel .attn-item[data-id^="extra"]')).to_have_count(12)
    expect(panel).to_be_hidden()
    lantern.dispatch_event("click")
    expect(panel).to_be_visible()
    away = (frame["x"] + frame["width"] + 200 if frame["x"] < 900 else 40, 800)
    assert page.evaluate("([x, y]) => document.elementFromPoint(x, y).id", away) == "world"   # the empty deck
    page.mouse.click(*away)
    expect(panel).to_be_hidden()
    lantern.dispatch_event("click")
    close.click()
    expect(panel).to_be_hidden()
    expect(lantern).to_be_focused()
    assert changed_deck.errors == []


def expect_need_you(page: Page, base_url: str) -> None:
    """The header's count is the open items, the same ones the lanterns stand for."""
    open_items = sum(state == "open" for state in attention_on_server(base_url).values())
    expect(page.locator("#needYou b")).to_have_text(str(open_items))


def act_on_every_item(page: Page, action: str, state: str) -> None:
    """Press an action on each listed item in turn, waiting for the pushed state each time."""
    for row in page.locator("#attnPanel .attn-item").all():
        row.locator(f'[data-act="{action}"]').click()
        expect(row).to_have_attribute("data-state", state)


def test_a_room_with_open_items_gets_one_lantern_with_a_count(deck: Deck, base_url: str) -> None:
    page = deck.page
    expect(page.locator(".lantern")).to_have_count(2)
    lantern = page.locator('.lantern[data-room="restoke"]')
    expect(lantern).to_have_count(1)
    expect(lantern).to_have_attribute("data-count", "2")   # the failed job and the session asking a question
    expect(lantern.locator("b")).to_have_text("2")
    expect(lantern).to_have_attribute("data-state", "open")
    expect(page.locator('.lantern[data-room="invoice-parser"] b')).to_have_text("")   # one item: no count
    expect(page.locator('.lantern[data-room="agent-fleet"]')).to_have_count(0)      # a running job and an idle session: nothing needs you
    assert rooms_by_name(page)["agent-fleet"]["attention"] is None
    expect(page.locator("#needYou b")).to_have_text("3")
    expect_need_you(page, base_url)

    before = attention_on_server(base_url)
    lantern.dispatch_event("click")
    expect(page.locator("#attnPanel")).to_be_visible()
    expect(page.locator("#attnPanel .attn-item")).to_have_count(2)
    expect(page.locator('#attnPanel .attn-item[data-kind="decision"] b')).to_contain_text("Keep the double fetch")
    page.locator('#attnPanel [data-owner="home:e1b5c8"]').click()
    expect(page.locator("#panelHead h2")).to_have_text("Upgrade Django to 5.2")
    expect(page.locator("#panelBody .steps li.failed")).to_contain_text("Bump Django and run the test suite")
    assert attention_on_server(base_url) == before   # reading and opening change nothing
    page.locator("#panel #close").click()
    page.keyboard.press("Escape")
    expect(page.locator("#attnPanel")).to_be_hidden()
    assert deck.errors == []


def test_acknowledging_dims_the_lantern_and_snoozing_hides_it(deck: Deck, base_url: str) -> None:
    page = deck.page
    lantern = page.locator('.lantern[data-room="restoke"]')
    lantern.dispatch_event("click")
    act_on_every_item(page, "acknowledge", "acknowledged")
    expect(lantern).to_have_class("lantern ack")
    expect(lantern).to_have_attribute("data-state", "acknowledged")
    expect(lantern).to_have_attribute("data-count", "2")
    assert set(attention_on_server(base_url).values()) == {"acknowledged", "open"}   # invoice-parser untouched
    expect(page.locator("#needYou b")).to_have_text("1")
    expect_need_you(page, base_url)

    act_on_every_item(page, "snooze", "snoozed")
    expect(lantern).to_have_count(0)
    assert rooms_by_name(page)["restoke"]["attention"] is None
    expect(page.locator("#needYou b")).to_have_text("1")
    act_on_every_item(page, "reopen", "open")
    expect(lantern).to_have_attribute("data-state", "open")
    expect(lantern).not_to_have_class("lantern ack")
    expect(page.locator("#needYou b")).to_have_text("3")
    page.keyboard.press("Escape")
    assert set(attention_on_server(base_url).values()) == {"open"}
    assert deck.errors == []


def test_failed_and_stalled_jobs_leave_the_floor_to_their_lantern(deck: Deck, base_url: str,
                                                                  fixture_data: dict[str, Any]) -> None:
    page = deck.page
    blocked = {f"{host['name']}:{job['id']}" for host in fixture_data["hosts"]
               for job in host["jobs"] if job["status"] in BLOCKED}
    assert blocked == {"home:e1b5c8", "worker:3c71d5"}
    assert not blocked & {agent["key"] for agent in page.evaluate("fleetDeck.agents()")}
    for key in blocked:
        expect(page.locator("#tags .tag", has_text=key.split(":")[1])).to_have_count(0)
    rooms = rooms_by_name(page)
    assert rooms["restoke"]["attention"] == {"kind": "blocker", "state": "open", "count": 2}
    assert rooms["invoice-parser"]["attention"] == {"kind": "blocker", "state": "open", "count": 1}
    expect(page.locator("#needYou b")).to_have_text("3")
    expect_need_you(page, base_url)
    assert deck.errors == []


@pytest.mark.parametrize("changed_deck", ["no-preference"], indirect=True)
def test_a_job_that_fails_or_stalls_leaves_the_floor_at_once(changed_deck: Deck, base_url: str) -> None:
    page = changed_deck.page
    blocked = {"worker:c90e11": "stalled", "home:b7d042": "failed"}
    page.evaluate("doc => fleetDeck.apply(doc)", finish_jobs(base_url, blocked))
    assert not page.evaluate(f"fleetDeck.agents().some(agent => {json.dumps(list(blocked))}.includes(agent.key))")
    expect(page.locator("#tags .tag", has_text="c90e11")).to_have_count(0)
    expect(page.locator("#tags .tag", has_text="b7d042")).to_have_count(0)
    expect(page.locator("#toggleFinished")).to_have_text("1 finished · show")   # blocked is not finished
    expect(page.locator("#needYou b")).to_have_text("3")
    assert changed_deck.errors == []


@pytest.mark.parametrize("changed_deck", ["no-preference"], indirect=True)
def test_a_failed_job_can_still_be_dismissed_from_its_panel(changed_deck: Deck) -> None:
    page = changed_deck.page
    page.locator('.lantern[data-room="restoke"]').dispatch_event("click")
    page.locator('#attnPanel [data-owner="home:e1b5c8"]').click()
    expect(page.locator("#panelHead h2")).to_have_text("Upgrade Django to 5.2")
    page.locator("#panel #dismiss").click()
    expect(page.locator("#panel")).not_to_have_class(re.compile("open"))
    chip = page.locator("#restoreDismissed")
    expect(chip).to_have_text("1 hidden · show")
    expect(page.locator('#attnPanel [data-owner="home:e1b5c8"]')).to_have_count(0)   # nothing to open while hidden
    expect(page.locator('.lantern[data-room="restoke"]')).to_have_attribute("data-count", "2")
    chip.click()
    expect(chip).to_have_count(0)
    page.locator('#attnPanel [data-owner="home:e1b5c8"]').click()
    expect(page.locator("#panel")).to_have_class(re.compile("open"))
    assert changed_deck.errors == []
