"""Browser smoke tests: the deck against the recorded fleet, at desktop and phone widths."""

import io
import json
import re
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta
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
from fleet.web.ingester import observe_runs

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
BLOCKED = {"failed", "lost", "stalled"}
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
    # A prior test may leave a nested floor route or another view on the shared page.
    deck.page.evaluate('fleetDeck.enterFloor(null)')
    deck.page.locator('#viewToggle [data-view="deck"]').click()
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
            fleetDeck.enterFloor(null);
            fleetDeck.lookAtRoom(null);
            fleetDeck.advanceTime(0);
        }""", original)
        deck.page.locator('#viewToggle [data-view="deck"]').click()


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
    expect(parent.locator('[data-now]')).to_contain_text('Now: 1 running')
    expect(parent.locator('[data-now] [data-now-item]')).to_have_text(['Port supplier list'])
    expect(parent.locator('[data-now] [data-job-chip]')).to_have_attribute('data-job-chip', 'home:route-job')
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
    expect(child.locator('[data-now] [data-now-item]')).to_have_text(['Port supplier list'])
    expect(child.locator('[data-now] [data-job-chip]')).to_have_attribute('data-job-chip', 'home:route-job')
    expect(child.locator('[data-attention-count]')).to_have_text('2 open attention items')
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


ROUTES_WORKTREE = {'toplevel': '/home/adi/Development/restoke-routes', 'linked_worktree': True,
            'repository': '/home/adi/Development/restoke', 'branch': 'feat/route-migration', 'detached': False,
            'head': '4be1c0d', 'dirty': 3, 'collected_at': 1790398500.0}


def test_epic_rows_and_cards_show_the_jobs_serving_them(changed_deck: Deck, route_migration, deck_state,
                                                         monkeypatch, request) -> None:
    """Each plan line shows its jobs as chips: host:id, a status glyph, the step and the branch; a chip opens its
    job's panel, and a finished line's latest job shows quieter."""
    store, milestones = route_migration['store'], route_migration['milestones']
    execution = open_execution(store)
    now = store.clock()
    # home:a1c3e9 is on the deck, step 2 of 2 running; its host reports a linked worktree.
    live = next(job for host in deck_state.fixture['hosts'] for job in host['jobs'] if job['id'] == 'a1c3e9')
    monkeypatch.setitem(live, 'workspace', ROUTES_WORKTREE)
    monkeypatch.setitem(live, 'workspace_reason', None)
    execution.link('home', 'a1c3e9', milestones[4].id, actor='user')
    execution.observe('home', JobObservation('a1c3e9', 'running', 'claude', now, None, now))
    # worker:d4f7a2 finished milestone 4's task earlier and has left the deck.
    execution.link('worker', 'd4f7a2', route_migration['milestones'][3].id, actor='user')
    execution.observe('worker', JobObservation('d4f7a2', 'done', 'claude', now - timedelta(hours=2),
                                               now - timedelta(hours=1), now))
    page = changed_deck.page
    page.evaluate("fleetDeck.enterFloor('restoke-v2')")
    card = page.locator('[data-epic-card]').nth(1)
    expect(card.locator('[data-now-item]')).to_have_text(['Port supplier list', '5. Milestone 5'])
    expect(card.locator('[data-now] [data-job-chip]')).to_have_count(2)
    fits = "r => r.scrollWidth <= r.clientWidth"   # chips shorten their branch rather than widen the page
    assert page.locator('#benchRoute').evaluate(fits)
    shoot(request, page, 'v3-epic-cards')
    card.get_by_role('button', name='Route migration', exact=True).click()

    items = page.locator('[data-epic-page] [data-plan-item]')
    fourth, fifth = items.nth(3).locator('[data-job-chip]'), items.nth(4).locator('[data-job-chip]')
    # Milestone 4: its task's running job, which no host reports, then its own finished one, quieter.
    expect(fourth).to_have_count(2)
    running, finished = fourth.nth(0), fourth.nth(1)
    expect(running).to_have_attribute('data-job-chip', 'home:route-job')
    expect(running).to_have_attribute('data-gone', '')
    expect(running.locator('[data-job-step]')).to_have_text('step ?')
    expect(running.locator('[data-job-branch]')).to_have_text('workspace unknown')
    expect(running.locator('[data-job-branch]')).to_have_attribute('title', 'the host is not reporting this job')
    expect(finished).to_have_attribute('data-job-chip', 'worker:d4f7a2')
    expect(finished).to_have_attribute('data-past', '')
    expect(finished.locator('.glyph')).to_have_attribute('data-action', 'done')
    expect(finished.locator('[data-job-step]')).to_have_text('step 2/2')
    assert float(finished.evaluate('e => getComputedStyle(e).opacity')) < 1
    # Milestone 5: the deck's job with its step, branch and worktree.
    expect(fifth).to_have_count(1)
    expect(fifth.locator('.id-chip')).to_have_text('a1c3e9')
    expect(fifth.locator('b')).to_have_text('home:')
    expect(fifth.locator('[data-job-step]')).to_have_text('step 2/2')
    expect(fifth.locator('[data-job-step]')).to_have_attribute('title', 'Step 2 of 2 serves 5. Milestone 5')
    expect(fifth.locator('[data-job-branch]')).to_have_text('feat/route-migration*')
    expect(fifth.locator('[data-job-branch]')).to_have_attribute(
        'title', 'feat/route-migration · worktree /home/adi/Development/restoke-routes of /home/adi/Development/restoke · 3 uncommitted')
    expect(fifth.locator('[data-job-branch] [data-copy]')).to_have_attribute('data-copy', ROUTES_WORKTREE['toplevel'])
    expect(page.locator('[data-epic-page]')).not_to_contain_text(ROUTES_WORKTREE['toplevel'])
    shoot(request, page, 'v3-epic-rows')
    if request.config.getoption('--shots'):
        page.set_viewport_size(VIEWPORTS['narrow'])
        fourth.first.scroll_into_view_if_needed()
        assert page.locator('#benchRoute').evaluate(fits)
        shoot(request, page, 'v3-epic-rows-narrow')
        page.set_viewport_size(VIEWPORTS['desktop'])

    # Copying the id or the path leaves the panel shut; the rest of the chip opens it.
    fifth.locator('.id-chip').click()
    fifth.locator('[data-job-branch] [data-copy]').click()
    expect(page.locator('#panel')).not_to_have_class(re.compile(r'\bopen\b'))
    finished.click()
    expect(page.locator('#panel')).not_to_have_class(re.compile(r'\bopen\b'))
    fifth.get_by_role('button', name='Open the job panel: home a1c3e9').click()
    expect(page.locator('#panel')).to_have_class(re.compile(r'\bopen\b'))
    assert page.evaluate("import('/js/model.js').then(m => m.selectedKey)") == 'home:a1c3e9'
    shoot(request, page, 'v3-chip-opens-panel')
    page.evaluate("import('/js/panel.js').then(m => m.closePanel())")
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
    expect(card.locator('[data-milestones] small')).to_have_text('6 of 8 milestones complete')
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


SUPPLIERS_WORKTREE = {"toplevel": "/home/adi/Development/restoke-suppliers", "linked_worktree": True,
            "repository": "/home/adi/Development/restoke", "branch": "feat/suppliers", "detached": False,
            "head": "abc1234", "dirty": 3, "collected_at": 1790399980.0}


def deck_job(base_url: str, job_id: str, **fields: Any) -> dict[str, Any]:
    """The server's state document with one of home's jobs changed, as its next stream update would bring it."""
    doc = finish_jobs(base_url, {})
    job, = [job for host in doc["hosts"] if host["name"] == "home" for job in host["jobs"] if job["id"] == job_id]
    job.update(fields)
    return doc


def test_the_panel_names_the_jobs_workspace_and_copies_its_path(changed_deck: Deck, base_url: str,
                                                                request: pytest.FixtureRequest) -> None:
    page = changed_deck.page
    page.evaluate('doc => fleetDeck.apply(doc)', deck_job(base_url, 'a1c3e9', workspace=SUPPLIERS_WORKTREE, workspace_reason=None))
    page.evaluate("fleetDeck.select('home:a1c3e9')")
    chip = page.locator('#panelHead [data-workspace]')
    expect(chip).to_have_text('restoke · restoke-suppliers · feat/suppliers @ abc1234 +3 uncommitted')
    expect(chip).to_have_attribute('title', re.compile('^/home/adi/Development/restoke-suppliers\nworktree of /home/adi/Development/restoke$'))
    copy = chip.get_by_role('button', name='Copy path /home/adi/Development/restoke-suppliers')
    copy.click()
    expect(copy).to_have_attribute('data-copied', '')
    shoot(request, page, 'v2-workspace-chip')

    page.locator('#panelTabs [data-tab="activity"]').click()
    expect(page.locator('#panelBody [data-tab="activity"] dl.meta dt')).to_have_text(
        ['ref', 'project', 'model', 'perms', 'updated'])
    page.locator('#panelTabs [data-tab="summary"]').click()

    main_checkout = {**SUPPLIERS_WORKTREE, "toplevel": "/home/adi/Development/restoke", "linked_worktree": False,
                     "branch": None, "detached": True, "dirty": 0}
    page.evaluate('doc => fleetDeck.apply(doc)', deck_job(base_url, 'a1c3e9', workspace=main_checkout))
    expect(chip).to_have_text('restoke · detached @ abc1234')
    expect(chip).to_have_attribute('title', '/home/adi/Development/restoke')

    page.set_viewport_size(VIEWPORTS["narrow"])
    page.evaluate('doc => fleetDeck.apply(doc)', deck_job(base_url, 'a1c3e9', workspace=SUPPLIERS_WORKTREE))
    box, head = chip.bounding_box(), page.locator('#panelHead').bounding_box()
    assert box['x'] + box['width'] <= head['x'] + head['width']
    shoot(request, page, 'v2-workspace-chip-narrow')
    page.locator('#panel #close').click()
    assert changed_deck.errors == []


def test_a_job_with_no_workspace_says_why(changed_deck: Deck, base_url: str, request: pytest.FixtureRequest) -> None:
    page = changed_deck.page
    page.evaluate("fleetDeck.select('home:a1c3e9')")   # the recorded fleet predates workspaces: no key at all
    chip = page.locator('#panelHead [data-workspace]')
    expect(chip).to_have_attribute('data-workspace', 'unknown')
    expect(chip).to_have_text('workspace unknown · not reported by this worker')
    expect(chip).to_have_attribute('title', '/home/adi/Development/restoke')
    expect(chip.get_by_role('button', name='Copy path /home/adi/Development/restoke')).to_be_visible()

    page.evaluate('doc => fleetDeck.apply(doc)',
                  deck_job(base_url, 'a1c3e9', workspace=None, workspace_reason='not a git repository'))
    expect(chip).to_have_text('workspace unknown · not a git repository')
    shoot(request, page, 'v2-workspace-unknown')
    page.locator('#panel #close').click()
    assert changed_deck.errors == []


def test_each_step_names_the_work_it_serves_and_opens_it(changed_deck: Deck, route_migration, base_url: str,
                                                         request: pytest.FixtureRequest) -> None:
    routes, milestones, store = route_migration['routes'], route_migration['milestones'], route_migration['store']
    open_execution(store).link('home', 'a1c3e9', routes.id, actor='user')
    job, = [job for host in finish_jobs(base_url, {})["hosts"] for job in host["jobs"] if job["id"] == 'a1c3e9']
    job["steps"][0]["work_item"], job["steps"][1]["work_item"] = milestones[2].id, milestones[3].id
    observe_runs(open_execution(store), open_library(store), {"name": "home", "ok": True, "jobs": {"a1c3e9": job}})
    # two more steps fleetd would carry: one for the job's own item, one for an item this deck's plan lacks
    steps = job["steps"] + [{"index": 2, "title": "Tidy up", "status": "pending", "work_item": routes.id},
                            {"index": 3, "title": "Elsewhere", "status": "pending", "work_item": "0198aaaa-gone"}]
    page = changed_deck.page
    page.evaluate('doc => fleetDeck.apply(doc)', deck_job(base_url, 'a1c3e9', steps=steps))
    page.evaluate("fleetDeck.select('home:a1c3e9')")
    expect(page.locator('#panel .work-crumbs')).to_have_text('V2 frontend overhaul > Route migration')
    page.locator('#panelTabs [data-tab="activity"]').click()
    rows = page.locator('#panelBody [data-tab="activity"] ol.steps > li')
    expect(rows).to_have_count(4)
    expect(rows.nth(0).locator('[data-step-work]')).to_have_text('for 3. Milestone 3')
    expect(rows.nth(1).locator('[data-step-work]')).to_have_text('for 4. Milestone 4')
    expect(rows.nth(2).locator('[data-step-work]')).to_have_count(0)
    expect(rows.nth(3).locator('[data-step-work]')).to_have_text("for item 0198aaaa not in this deck's plan")
    expect(page.locator('ol.steps > li[aria-current="step"]')).to_have_count(1)
    expect(rows.nth(1)).to_have_attribute('aria-current', 'step')
    expect(rows.nth(1).locator('.now')).to_have_text('now')
    shoot(request, page, 'v2-step-work')

    rows.nth(1).get_by_role('button', name='4. Milestone 4').click()
    route = page.locator('#benchRoute')
    expect(route).to_have_attribute('data-level', 'bench')
    expect(route.get_by_role('heading', level=2)).to_have_text('4. Milestone 4')
    page.keyboard.press('Escape')
    expect(route).to_have_attribute('data-level', 'room')
    page.evaluate('fleetDeck.enterFloor(null)')
    page.locator('#panelTabs [data-tab="summary"]').click()
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
    page.get_by_role('button', name='Save version 1', exact=True).click()
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
    page.get_by_role('button', name='Save version 1', exact=True).click()
    charter = route.locator('[data-guidance="charter"]')
    expect(charter.locator('[data-inherits]')).to_have_text('Inherits constitution version 1')
    expect(decisions.first.locator('[data-promote]')).to_have_text('Promote to charter')
    expect(route.locator('[data-promote-consequence]')).to_contain_text('new charter version')
    shoot(request, page, 'batch7-promote-before')
    if request.config.getoption('--shots'):
        page.set_viewport_size(VIEWPORTS['narrow'])
        decisions.first.locator('[data-promote]').scroll_into_view_if_needed()
        shoot(request, page, 'batch7-promote-narrow-before')
        page.set_viewport_size(VIEWPORTS['desktop'])
    decisions.first.get_by_role('button', name='Promote to charter').click()
    expect(route.locator('[data-guidance-feedback]')).to_contain_text('Promoted decision to charter version 2')
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
    page.get_by_role('button', name='Save version 3', exact=True).click()
    expect(charter.locator('[role="alert"]')).to_contain_text('it is now version 3')
    alert, panel = charter.locator('[role="alert"]').bounding_box(), route.bounding_box()
    assert alert['y'] + alert['height'] <= panel['y'] + panel['height'], (alert, panel)   # not lost under the long editor
    expect(page.locator('[data-guidance-text]')).to_have_value('Edited elsewhere first.')
    shoot(request, page, 'guidance-conflict')
    # the stale save is refused with a 409 on purpose; the browser logs it, which is expected here, not a page error
    changed_deck.errors[:] = [e for e in changed_deck.errors if '409 (Conflict)' not in e]
    with page.expect_event('dialog') as prompt:
        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_role('button', name='Discard', exact=True).click()
    assert prompt.value.type == 'confirm'
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
    if page.viewport_size["width"] <= 760:
        expect(page.locator("#attnPanel")).to_be_hidden()
    else:
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
    expect(badge).to_have_attribute("title", "6 agents here; click to spread them. Click elsewhere to regroup.")
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
    expect(page.locator("#workingOpen")).to_have_text("5 running · list")   # still counted
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
    expect(page.locator("#toast")).to_contain_text("Its androids are hidden; running work continues")
    dim, _, warm = room_colour(page, "restoke")
    assert dim < bright * 0.75 and warm > cool + 20                            # its work runs: dim, but lit warm
    assert crew(page, "restoke") == set()
    expect(page.locator("#tags .tag", has_text="c90e11")).to_have_count(0)
    assert rooms_by_name(page)["restoke"]["lit"] is True
    lantern = page.locator('.lantern[data-room="restoke"]')                    # the lantern is unaffected
    expect(lantern).to_have_attribute("data-count", "2")
    lantern.dispatch_event("click")
    page.locator(f'#attnPanel [data-owner="{ASKING}"]').click()
    expect(page.locator("#panelHead .chip.sess")).to_have_text("live · idle")
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
    for name in ("Allow these for this job", "Allow all Bash for this job", "Resolve without allowing"):
        expect(body.get_by_role("button", name=name, exact=True)).to_be_enabled()
    shoot(request, page, "batch3-refusals")
    page.set_viewport_size(VIEWPORTS["narrow"])
    shoot(request, page, "batch3-refusals-390")
    dialogs = []
    def cancel_grant(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()
    page.once("dialog", cancel_grant)
    body.get_by_role("button", name="Allow all Bash for this job", exact=True).click()
    assert len(dialogs) == 1
    assert "any Bash command" in dialogs[0]
    assert sent == []
    assert server.state.attention.get(item["id"]).state == "open"
    page.once("dialog", lambda dialog: dialog.accept())
    body.get_by_role("button", name="Allow all Bash for this job", exact=True).click()
    expect(body.locator(".refusal-done")).to_have_text(
        "allowed for job j1: Bash; step 2 continues as step 3; still not allowed: /etc/restoke.conf")
    assert sent == [["Bash"]]
    assert server.state.attention.get(item["id"]).state == "resolved"
    expect(body.get_by_role("button", name="Resolve without allowing", exact=True)).to_be_disabled()
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
        expect(body.locator(".decision-receipt")).to_have_text("Answer recorded as a Decision.")
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


def running_state(base_url: str, route_migration: dict[str, Any]) -> dict[str, Any]:
    """The fleet with two jobs linked to Route migration's work, one on a step serving a milestone of its own, a
    blocked job, and workspaces as a current fleetd reports them (or the reason it could not)."""
    execution = open_execution(open_store())
    milestones = route_migration["milestones"]
    execution.link("home", "a1c3e9", milestones[2].id, actor="user")
    execution.link("worker", "c90e11", route_migration["redirect"].id, actor="user")
    with urlopen(base_url + "/api/state", timeout=5) as response:
        doc = json.load(response)
    jobs = {(host["name"], job["id"]): job for host in doc["hosts"] for job in host["jobs"]}
    assert jobs["home", "a1c3e9"]["work"] is not None and jobs["worker", "c90e11"]["work"] is not None
    chain = [{"id": item.id, "kind": item.kind, "title": item.title}
             for item in (route_migration["routes"], milestones[3])]
    chain.insert(0, jobs["home", "a1c3e9"]["work"]["chain"][0])
    jobs["home", "b7d042"]["work"] = {"project": "restoke-v2", "chain": [], "step": {"index": 1, "chain": chain}}
    jobs["home", "b7d042"].update(workspace_reason=None, workspace={
        "toplevel": "/home/adi/src/restoke-flaky-upload", "linked_worktree": True, "repository": "/home/adi/src/restoke",
        "branch": "fix/flaky-upload", "detached": False, "head": "abc1234", "dirty": 3, "collected_at": doc["time"]})
    jobs["home", "a1c3e9"].update(workspace=None, workspace_reason="not a git repository")
    blocked = jobs["home", "e1b5c8"]
    blocked["status"] = "blocked"
    blocked["steps"][0]["status"] = "blocked"
    return doc


def test_the_running_view_lists_live_jobs_by_project_and_work(changed_deck: Deck, route_migration, base_url: str,
                                                              request) -> None:
    page = changed_deck.page
    doc = running_state(base_url, route_migration)
    page.evaluate("doc => fleetDeck.apply(doc)", doc)
    chip, panel = page.locator("#workingOpen"), page.locator("#runPanel")
    expect(chip).to_have_text("4 running · list")
    # done counts the finished job that left the deck; failed has a chip of its own
    expect(page.locator("#stats .chip", has_text="done")).to_have_text("1 done")
    expect(page.locator("#failedJobs")).to_have_text("1 failed")
    chip.click()
    expect(panel).to_be_visible()
    expect(chip).to_have_attribute("aria-expanded", "true")
    expect(panel.locator(".run-sum")).to_have_text("4 running · 1 blocked · 1 failed · 1 queued")
    # restoke's linked jobs under their work path; a task sits under its parent's path and names itself on the row
    restoke = panel.locator('.run-proj-g[data-project="restoke"]')
    expect(restoke.locator("h4")).to_have_text("restoke")
    expect(restoke.locator(".run-path h5")).to_have_text([
        "V2 frontend overhaul > Route migration", "V2 frontend overhaul > Route migration > 3. Milestone 3",
        "V2 frontend overhaul > Route migration > 4. Milestone 4"])
    paths = restoke.locator(".run-path")
    expect(paths.nth(0).locator(".run-row")).to_have_attribute("data-key", "worker:c90e11")
    expect(paths.nth(0).locator(".run-work")).to_have_text("◆ Fix redirect loop")
    expect(paths.nth(1).locator(".run-row")).to_have_attribute("data-key", "home:a1c3e9")
    step_row = paths.nth(2).locator(".run-row")   # linked through its running step alone
    expect(step_row).to_have_attribute("data-key", "home:b7d042")
    expect(step_row.locator(".run-work")).to_have_attribute("title", re.compile("^step 2 serves this milestone"))
    expect(step_row.locator(".run-m")).to_contain_text("home:b7d042")
    expect(step_row.locator(".run-m")).to_contain_text(re.compile(r"step 2/3 · \d+"))
    workspace = step_row.locator(".run-ws")
    expect(workspace.locator("span, small, em")).to_have_text(["fix/flaky-upload", "restoke-flaky-upload", "±3"])
    expect(workspace).to_have_attribute("data-copy-id", "/home/adi/src/restoke-flaky-upload")
    expect(workspace).to_have_attribute("title", re.compile("worktree /home/adi/src/restoke-flaky-upload of /home/adi/src/restoke"))
    unknown = paths.nth(1).locator(".run-ws")
    expect(unknown).to_have_text("workspace unknown")
    expect(unknown).to_have_attribute("title", "workspace unknown: not a git repository")
    expect(panel.locator('[data-key="worker:c90e11"] .run-ws')).to_have_attribute(
        "title", "workspace unknown: not reported by this worker")
    # the unlinked ones apart, saying what they need and how to give it; failed jobs are listed, finished ones not
    unlinked = panel.locator("[data-unlinked]")
    expect(unlinked.locator("h4")).to_have_text("Not linked to work")
    expect(unlinked.locator(".run-need")).to_have_text("Jobs without a linked work item. The dispatching agent can link them with fleet run link.")
    expect(unlinked.locator(".run-row")).to_have_count(4)
    for key, status in {"home:e1b5c8": "blocked", "worker:f20a6d": "running", "worker:0a9e3b": "queued",
                        "worker:3c71d5": "failed"}.items():
        expect(unlinked.locator(f'.run-row[data-key="{key}"]')).to_have_attribute("data-status", status)
    expect(unlinked.locator('[data-key="worker:0a9e3b"] .run-m')).to_contain_text("step 1/2")
    expect(unlinked.locator('[data-key="worker:f20a6d"] .run-link')).to_have_attribute(
        "data-copy-id", "fleet run link worker f20a6d <work-item>")
    expect(panel.locator('[data-key="worker:d4f7a2"]')).to_have_count(0)
    shoot(request, page, "running-view")

    # live: a state update re-renders in place and keeps the scroll
    panel.evaluate("el => { el.style.maxHeight = '220px'; el.scrollTop = 120; }")
    top = panel.evaluate("el => el.scrollTop")
    assert top > 0, "the list should scroll at this height"
    finished = json.loads(json.dumps(doc))
    next(job for host in finished["hosts"] for job in host["jobs"] if job["id"] == "c90e11")["status"] = "done"
    next(job for host in finished["hosts"] for job in host["jobs"] if job["id"] == "3c71d5")["status"] = "lost"
    page.evaluate("doc => fleetDeck.apply(doc)", finished)
    expect(panel.locator('[data-key="worker:c90e11"]')).to_have_count(0)
    expect(panel.locator(".run-sum")).to_have_text("3 running · 1 blocked · 1 lost · 1 queued")
    expect(panel.locator('.run-row[data-key="worker:3c71d5"]')).to_have_attribute("data-status", "lost")
    expect(page.locator("#failedJobs")).to_have_text("1 failed")   # a lost job counts as failed
    assert "worker:3c71d5" not in {agent["key"] for agent in page.evaluate("fleetDeck.agents()")}   # its lantern carries it
    expect(page.locator("#stats .chip", has_text="done")).to_have_text("2 done")
    assert panel.evaluate("el => el.scrollTop") == top
    panel.evaluate("el => { el.style.maxHeight = ''; }")

    # a row opens its job's panel; Escape and a click away close the list
    panel.locator('.run-row[data-key="home:a1c3e9"] .run-b > b').click()
    expect(panel).to_be_hidden()
    expect(page.locator("#panel")).to_have_attribute("aria-hidden", "false")
    expect(page.locator("#panelHead h2")).to_have_text(re.compile("^Migrate the suppliers list"))
    page.locator("#panel #close").click()
    chip.click()
    expect(panel).to_be_visible()
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(chip).to_be_focused()
    expect(chip).to_have_attribute("aria-expanded", "false")
    chip.click()
    page.mouse.click(700, 820)
    expect(panel).to_be_hidden()

    # with nothing live, it says so
    for host in finished["hosts"]:
        for job in host["jobs"]:
            job["status"] = "done"
    page.evaluate("doc => fleetDeck.apply(doc)", finished)
    chip.click()
    expect(panel.locator(".run-empty")).to_have_text("Nothing is running, blocked, failed or queued.")
    page.keyboard.press("Escape")

    page.evaluate("doc => fleetDeck.apply(doc)", doc)
    page.set_viewport_size(VIEWPORTS["narrow"])
    chip.click()
    box = panel.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= VIEWPORTS["narrow"]["width"]
    shoot(request, page, "running-view-narrow")
    page.keyboard.press("Escape")
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
    page.once('dialog', lambda dialog: dialog.accept())
    chip.click()
    expect(chip).to_have_count(0)
    page.locator('#attnPanel [data-owner="home:e1b5c8"]').click()
    expect(page.locator("#panel")).to_have_class(re.compile("open"))
    assert changed_deck.errors == []


def test_audit1_batch3_choices_preserve_draft(changed_deck: Deck, request) -> None:
    page = changed_deck.page
    page.route('**/api/decision?*', lambda route: route.fulfill(json={
        'question': 'Which route?', 'context': 'Route review', 'options': ['Direct', 'Scenic'],
        'proposal': None}))
    page.evaluate("""async () => (await import('/js/reader.js')).openAttentionReader({
        id: 'batch3', kind: 'decision', summary: 'Which route?', source: 'manual',
        source_reference: 'route-review', context_reference: 'Route review', state: 'open', last_seen: 200})""")
    answer = page.get_by_label('Your answer', exact=True)
    expect(answer).to_be_visible()
    page.get_by_role('radio', name='Direct', exact=True).check()
    expect(answer).to_have_value('Direct')
    page.get_by_role('radio', name='Scenic', exact=True).check()
    expect(answer).to_have_value('Scenic')
    answer.fill('Take the coast with a stop')
    page.get_by_role('radio', name='Direct', exact=True).check()
    expect(answer).to_have_value('Take the coast with a stop')
    page.get_by_role('radio', name='Scenic', exact=True).check()
    expect(answer).to_have_value('Take the coast with a stop')
    expect(page.locator('#rdKind')).to_have_text('QUESTION')
    expect(page.locator('#rdBody')).to_contain_text('cannot be undone')
    shoot(request, page, 'batch3-question')
    page.set_viewport_size(VIEWPORTS['narrow'])
    shoot(request, page, 'batch3-question-390')
    page.get_by_role('button', name='Close reader', exact=True).click()
    page.unroute('**/api/decision?*')


def test_audit1_batch3_blocker_names_where_to_act(changed_deck: Deck, request) -> None:
    page = changed_deck.page
    page.evaluate("""async () => (await import('/js/reader.js')).openAttentionReader({
        id: 'batch3-blocker', kind: 'blocker', summary: 'Step failed', source: 'host-stream',
        context_reference: 'home:j1', state: 'open', last_seen: 200})""")
    expect(page.locator('#rdBody')).to_contain_text('No answer form is available')
    expect(page.locator('#rdBody')).to_contain_text('Open job or Open session')
    expect(page.locator('#rdBody')).to_contain_text('does not restart the job')
    expect(page.locator('#rdKind')).to_have_text('BLOCKER')
    shoot(request, page, 'batch3-blocker')
    page.set_viewport_size(VIEWPORTS['narrow'])
    shoot(request, page, 'batch3-blocker-390')


def test_audit1_batch4_attention_resolve_undo(changed_deck: Deck, base_url: str, request) -> None:
    page = changed_deck.page
    page.locator('.lantern[data-room="restoke"]').dispatch_event('click')
    row = page.locator('#attnPanel .attn-item[data-kind="blocker"]')
    expect(row.locator('small')).to_contain_text('Failed')
    expect(page.locator('.lantern[data-room="invoice-parser"]')).to_have_attribute('title', re.compile('failed'))
    expect(row.locator('[data-act="acknowledge"]')).to_have_attribute('title', re.compile('dims'))
    expect(row.locator('.attn-consequence')).to_contain_text('does not answer')
    shoot(request, page, 'batch4-attention')
    page.set_viewport_size(VIEWPORTS['narrow'])
    page.wait_for_function("(() => { const b = document.getElementById('attnPanel').getBoundingClientRect(); return b.left >= 0 && b.right <= innerWidth; })()")
    shoot(request, page, 'batch4-attention-390')
    row.locator('[data-act="resolve"]').click()
    expect(row).to_have_count(0)
    expect(page.locator('#toast')).to_contain_text('work is unchanged')
    shoot(request, page, 'batch4-resolved-390')
    page.locator('#toast [data-undo]').click()
    expect(row).to_have_attribute('data-state', 'open')
    expect(page.locator('#toast')).to_contain_text('restored')
    shoot(request, page, 'batch4-restored-390')
    assert changed_deck.errors == []


def test_p5_all_rooms_attention_from_header(changed_deck: Deck, base_url: str, request) -> None:
    from fleet.projections.attention import attention_display
    page = changed_deck.page
    with urlopen(base_url + '/api/state', timeout=5) as response:
        document = json.load(response)
    model = document['attention'][0]
    document['attention'] += [
        {**model, 'id': 'p5-front-desk', 'project': None, 'project_id': None,
         'summary': 'Visitor needs a route', 'owner': None, 'state': 'open', 'blocked': False},
        {**model, 'id': 'p5-owned', 'project': None, 'project_id': None,
         'summary': 'Agent-owned request', 'owner': 'dispatcher', 'state': 'open', 'blocked': False},
        {**model, 'id': 'p5-ack', 'summary': 'Already seen', 'state': 'acknowledged'},
        {**model, 'id': 'p5-snooze', 'summary': 'Waiting until later', 'state': 'snoozed',
         'snoozed_until': document['time'] + 3600},
        {**model, 'id': 'p5-resolved', 'summary': 'Already resolved', 'state': 'resolved'}]
    document['attention_display'] = attention_display(document['attention'], document['building'], document['projects'])
    page.evaluate('doc => fleetDeck.apply(doc)', document)
    chip = page.get_by_role('button', name=re.compile(r'\d+ need you'))
    chip.click()
    panel = page.locator('#attnPanel')
    expect(panel).to_have_attribute('data-scope', 'all')
    expect(panel.locator('[data-attention-group="open"] .attn-item')).to_have_count(5)
    expect(panel.locator('[data-attention-group="open"] [data-id="p5-resolved"]')).to_have_count(0)
    expect(panel.locator('[data-attention-group="resolved"] [data-id="p5-resolved"]')).to_have_attribute('data-state', 'resolved')
    expect(panel.locator('[data-id="p5-front-desk"]')).to_contain_text('Owner not reported')
    expect(panel.locator('[data-id="p5-owned"]')).to_contain_text('dispatcher')
    expect(panel.locator('[data-id="p5-owned"] .attn-place')).to_contain_text('Front desk')
    expect(panel.locator('.attn-age').first).to_contain_text('ago')
    expect(panel).to_contain_text('Undo is available for 6 seconds')
    expect(panel.locator('[data-attention-group="other"]')).not_to_have_attribute('open', '')
    panel.locator('[data-attention-group="other"] summary').click()
    expect(panel.locator('[data-id="p5-ack"]')).to_be_visible()
    expect(panel.locator('[data-id="p5-snooze"]')).to_be_visible()
    panel.evaluate('el => { el.scrollTop = 0; }')
    shoot(request, page, 'p5-all-rooms')
    page.set_viewport_size(VIEWPORTS['narrow'])
    page.wait_for_function("document.getElementById('attnPanel').getBoundingClientRect().right <= innerWidth")
    panel.evaluate('el => { el.scrollTop = 0; }')
    shoot(request, page, 'p5-all-rooms-390')
    page.keyboard.press('Escape')
    expect(panel).to_be_hidden()
    expect(page.locator('#needYou')).to_be_focused()
    page.locator('#needYou').click()
    expect(panel).to_be_visible()
    page.keyboard.press('Escape')
    assert changed_deck.errors == []


def test_p5_global_attention_empty_and_preserves_building(changed_deck: Deck, base_url: str) -> None:
    from fleet.projections.attention import attention_display
    page = changed_deck.page
    page.locator('#viewToggle [data-view="building"]').click()
    saved = page.evaluate("localStorage.getItem('fleet.view')")
    page.locator('#needYou').click()
    panel = page.locator('#attnPanel')
    expect(panel).to_be_visible()
    expect(page.locator('body')).to_have_attribute('data-view', 'building')
    with urlopen(base_url + '/api/state', timeout=5) as response:
        document = json.load(response)
    document['attention'] = []
    document['attention_display'] = attention_display([], document['building'], document['projects'])
    page.evaluate('doc => fleetDeck.apply(doc)', document)
    expect(panel).to_contain_text('No open attention items')
    expect(page.locator('#needYou')).to_have_attribute('aria-expanded', 'true')
    assert page.evaluate("localStorage.getItem('fleet.view')") == saved
    page.keyboard.press('Escape')
    expect(page.locator('#needYou')).to_be_focused()
    page.locator('#viewToggle [data-view="deck"]').click()


def test_p5_global_actions_and_owner_navigation(changed_deck: Deck, base_url: str) -> None:
    page = changed_deck.page
    page.locator('#viewToggle [data-view="building"]').click()
    saved = page.evaluate("localStorage.getItem('fleet.view')")
    page.locator('#needYou').click()
    panel = page.locator('#attnPanel')
    row = panel.locator('.attn-item', has=page.locator('[data-owner="home:e1b5c8"]'))
    item_id = row.get_attribute('data-id')
    row.locator('[data-act="acknowledge"]').click()
    expect(panel.locator('[data-attention-group="open"] .attn-item')).to_have_count(2)
    expect(panel.locator('.attn-status')).to_contain_text('Acknowledged')
    if panel.locator('[data-attention-group="other"]').get_attribute('open') is None:
        panel.locator('[data-attention-group="other"] summary').click()
    row = panel.locator(f'[data-id="{item_id}"]')
    expect(row).to_have_attribute('data-state', 'acknowledged')
    row.locator('[data-act="reopen"]').click()
    expect(panel.locator('[data-attention-group="open"] .attn-item')).to_have_count(3)
    row.locator('[data-act="resolve"]').click()
    expect(row).to_have_attribute('data-state', 'resolved')
    expect(panel.locator('[data-attention-group="open"] .attn-item')).to_have_count(2)
    expect(row.locator('[data-act]')).to_have_count(0)
    page.locator('#toast [data-undo]').click()
    expect(row).to_have_attribute('data-state', 'open')
    expect(page.locator('#needYou')).to_have_attribute('aria-expanded', 'true')
    expect(row.locator('[data-owner]')).to_have_attribute('title', re.compile('whole deck'))
    row.locator('[data-owner]').click()
    expect(panel).to_be_hidden()
    expect(page.locator('body')).to_have_attribute('data-view', 'deck')
    expect(page.locator('#panelHead h2')).to_have_text('Upgrade Django to 5.2')
    assert page.evaluate("localStorage.getItem('fleet.view')") == saved
    page.locator('#panel #close').click()
    page.locator('#viewToggle [data-view="deck"]').click()
    assert changed_deck.errors == []


def test_audit1_batch6_running_navigation_preserves_saved_view(changed_deck: Deck) -> None:
    page = changed_deck.page
    page.locator('#viewToggle [data-view="building"]').click()
    saved = page.evaluate("localStorage.getItem('fleet.view')")
    page.locator('#workingOpen').click()
    page.locator('#runPanel .run-row[data-key="home:a1c3e9"]').click()
    expect(page.locator('body')).to_have_attribute('data-view', 'deck')
    expect(page.locator('#panel')).to_have_class('open')
    assert page.evaluate("localStorage.getItem('fleet.view')") == saved
    page.locator('#panel #close').click()
    page.locator('#viewToggle [data-view="deck"]').click()


def test_audit1_batch6_header_and_focus_copy(changed_deck: Deck, request) -> None:
    page = changed_deck.page
    expect(page.locator('#workingOpen')).to_have_text('4 running · list')
    expect(page.locator('#stats .chip-inert')).to_have_count(4)
    expect(page.locator('.focus-switch[data-room="restoke"] [data-set="background"]')).to_have_attribute('title', re.compile('androids.*Running work continues', re.I))
    shoot(request, page, 'batch6-header-focus')
    page.set_viewport_size(VIEWPORTS['narrow'])
    shoot(request, page, 'batch6-header-focus-390')


def test_audit1_batch6_move_consequences_and_feedback(changed_deck: Deck, request) -> None:
    page = changed_deck.page
    sent = []
    def move(route):
        sent.append(route.request.post_data_json)
        route.fulfill(json={'host': 'home', 'id': 'a1c3e9', 'project': 'invoice-parser',
                            'project_id': 'p-1c0ce5a2', 'linked': True})
    page.route('**/api/agent/move', move)
    try:
        page.locator('#tags .tag', has_text='a1c3e9').click()
        page.locator('#moveAgent').click()
        menu = page.locator('#moveMenu')
        expect(menu.locator('.move-consequence')).to_contain_text('Move back')
        expect(menu).to_contain_text('fleet project unlink home:invoice-parser')
        shoot(request, page, 'batch6-move')
        page.set_viewport_size(VIEWPORTS['narrow'])
        page.locator('#moveAgent').click()
        page.locator('#moveAgent').click()
        shoot(request, page, 'batch6-move-390')
        menu.get_by_role('menuitem', name=re.compile('Invoice analysis')).click()
        expect(page.locator('#toast')).to_contain_text('Invoice analysis')
        expect(page.locator('#toast')).to_contain_text('Linked home:invoice-parser')
        assert sent == [{'host': 'home', 'id': 'a1c3e9', 'project': 'p-1c0ce5a2'}]
    finally:
        page.unroute('**/api/agent/move', move)
        page.keyboard.press('Escape')


def test_audit1_batch6_hidden_restore_says_and_confirms_its_effect(changed_deck: Deck) -> None:
    page = changed_deck.page
    page.locator('.lantern[data-room="restoke"]').dispatch_event('click')
    page.locator('#attnPanel [data-owner="home:e1b5c8"]').click()
    page.locator('#dismiss').click()
    chip = page.locator('#restoreDismissed')
    expect(chip).to_have_attribute('title', re.compile('permanently forget'))
    page.once('dialog', lambda dialog: dialog.dismiss())
    chip.click()
    expect(chip).to_be_visible()
    page.once('dialog', lambda dialog: dialog.accept())
    chip.click()
    expect(chip).to_have_count(0)
    expect(page.locator('#toast')).to_contain_text('dismissals forgotten')


def test_batch11_project_decisions_and_room_alerts(changed_deck: Deck, route_migration, request) -> None:
    from fleet.composition import open_decisions, open_attention
    store = route_migration['store']
    task = route_migration['redirect']
    decision = open_decisions(store).record_guided(task.id, actor='reviewer', question='Keep redirects?',
        answer='Keep them for old bookmarks.', principle='Compatibility')
    alert = open_attention(store).raise_item(project='restoke-v2', work_item=task.id, kind='alert', owner='user',
        source='manual', source_reference='batch11', headline='Redirect audit complete', context_reference='audit.md', actor='agent')
    question = open_attention(store).raise_item(project='restoke-v2', kind='decision', owner='user',
        source='manual', source_reference='batch11-unlinked', headline='Ship the banner?',
        context_reference='banner.md', actor='agent')
    unlinked = open_decisions(store).answer(question.id, 'Wait for review.', actor='user')
    page = changed_deck.page
    page.evaluate("fleetDeck.enterFloor('restoke-v2')")
    page.get_by_role('button', name='Project decisions', exact=True).click()
    expect(page.locator(f'[data-decision="{unlinked.id}"]')).to_contain_text('No linked work item')
    expect(page.locator('#benchRoute [data-decisions]')).to_contain_text('Keep redirects?')
    expect(page.locator(f'[data-decision="{decision.id}"]')).to_contain_text('reviewer')
    shoot(request, page, 'batch11-project-decisions')
    page.set_viewport_size(VIEWPORTS['narrow'])
    shoot(request, page, 'batch11-project-decisions-390')
    page.locator('[data-back-floor]').click()
    page.get_by_role('button', name='Route migration', exact=True).click()
    expect(page.locator(f'#benchRoute [data-room-attention] [data-attention="{alert.id}"]')).to_contain_text('Redirect audit complete')
    expect(page.locator('#benchRoute [data-guidance="charter"]')).to_contain_text('No charter recorded.')
    expect(page.locator(f'#benchRoute [data-decision="{decision.id}"]')).to_be_visible()
    expect(page.locator(f'#benchRoute [data-decision="{unlinked.id}"]')).to_have_count(0)
    page.locator('[data-room-attention]').evaluate("el => el.scrollIntoView({block: 'center'})")
    shoot(request, page, 'batch11-room-alert-390')
    page.set_viewport_size(VIEWPORTS['desktop'])
    page.locator('[data-room-attention]').evaluate("el => el.scrollIntoView({block: 'center'})")
    shoot(request, page, 'batch11-room-alert')
    page.evaluate('fleetDeck.enterFloor(null)')
    assert changed_deck.errors == []


@pytest.mark.parametrize('kind', ['constitution', 'charter'])
@pytest.mark.parametrize('viewport', ['desktop', 'narrow'])
def test_batch7_guidance_draft_exits(changed_deck: Deck, deck_state, monkeypatch, tmp_path,
                                    request: pytest.FixtureRequest, kind: str, viewport: str) -> None:
    """Rejecting any discard preserves the draft and route; accepting never writes guidance."""
    store = open_store()
    monkeypatch.setattr(deck_state, 'store', store)
    monkeypatch.setattr(deck_state, 'attention', open_attention(store))
    epic = open_work(store).add(project='restoke-v2', title='Guidance test', goal='Keep guidance safe.', kind='epic', actor='user')
    repo = tmp_path / 'management'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init'], check=True, capture_output=True, timeout=10)
    records = open_records(store)
    records.register('restoke-v2', repo, actor='user')
    page = changed_deck.page
    page.set_viewport_size(VIEWPORTS[viewport])
    route = page.locator('#benchRoute')
    text = page.locator('[data-guidance-text]')
    dialogs = []
    accepting = False

    def handle_dialog(dialog) -> None:
        dialogs.append((dialog.type, dialog.message))
        dialog.accept() if accepting else dialog.dismiss()

    def open_editor() -> None:
        page.evaluate("async () => { (await import('/js/building.js')).showView('floor', 1); await fleetDeck.enterFloor('restoke-v2'); }")
        expect(route).to_have_attribute('data-level', 'floor')
        if kind == 'constitution':
            page.locator('[data-open-constitution]').click()
        else:
            route.get_by_role('button', name='Guidance test', exact=True).click()
        page.get_by_role('button', name=f'Write the {kind}').click()
        expect(text).to_have_value('')

    def exit_editor(exit: str) -> None:
        if exit == 'header':
            page.locator('#viewToggle [data-view=deck]').click()
        elif exit in ('lift', 'storehouse'):
            button = page.locator('#lift [data-lift=' + ('L' if exit == 'lift' else 'S') + ']')
            # The expanded plan covers the lift on phones; exercise its handler there.
            button.dispatch_event('click') if viewport == 'narrow' else button.click()
        elif exit == 'escape':
            page.keyboard.press('Escape')
        else:
            page.locator({'discard': '[data-guidance-cancel]', 'floor': '[data-back-floor]',
                          'room': '[data-back-room]'}[exit]).click()

    page.on('dialog', handle_dialog)
    try:
        for exit in ['discard', 'escape', 'floor', 'header', 'lift', 'storehouse'] + (['room'] if kind == 'charter' else []):
            open_editor()
            draft = f'# Unsaved {kind}\n\nKeep this {exit} draft.'
            text.fill(draft)
            before = route.get_attribute('data-level')
            exit_editor(exit)
            assert len(dialogs) == 1, (exit, dialogs)
            assert dialogs[-1][0] == 'confirm'
            assert 'cannot be recovered' in dialogs[-1][1]
            expect(text).to_have_value(draft)
            expect(route).to_have_attribute('data-level', before)
            dialogs.clear()
            accepting = True
            exit_editor(exit)
            assert len(dialogs) == 1
            expect(text).to_have_count(0)
            if exit not in ('header', 'lift', 'storehouse'):
                expect(route.locator('[data-guidance-feedback]')).to_contain_text('Discarded')
            else:
                expect(page.locator('#toast')).to_contain_text('Discarded')
            assert records.guidance('restoke-v2', epic=epic.id if kind == 'charter' else None) is None
            dialogs.clear()
            accepting = False
        open_editor()
        # Untouched empty drafts close without demanding attention.
        page.locator('[data-guidance-cancel]').click()
        assert dialogs == []
        open_editor()
        text.fill('# Safe guidance\n\nAgents follow these rules.')
        expect(page.locator('[data-guidance-save]')).to_have_text('Save version 1')
        expect(page.locator('[data-guidance-cancel]')).to_have_text('Discard')
        expect(route.locator('[data-guidance-scope]')).to_contain_text('epic' if kind == 'charter' else 'project')
        shoot(request, page, f'audit2-batch1-{kind}-{viewport}-editor')
        page.locator('[data-guidance-save]').scroll_into_view_if_needed()
        for control in ['[data-guidance-save]', '[data-guidance-cancel]']:
            box = page.locator(control).bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= VIEWPORTS[viewport]['width'], box
        shoot(request, page, f'audit2-batch1-{kind}-{viewport}-actions')
        pending = []
        page.route('**/api/guidance', lambda request: pending.append(request))
        page.locator('[data-guidance-save]').click()
        expect(page.locator('[data-guidance-save]')).to_be_disabled()
        expect(page.locator('[data-guidance-cancel]')).to_be_disabled()
        page.keyboard.press('Escape')
        page.locator('[data-back-floor]').click()
        page.locator('#viewToggle [data-view=deck]').click()
        exit_editor('lift')
        exit_editor('storehouse')
        expect(text).to_have_value('# Safe guidance\n\nAgents follow these rules.')
        assert dialogs == []
        assert len(pending) == 1
        with page.expect_response(lambda response: response.request.method == 'POST'
                                  and response.url.endswith('/api/guidance')) as saved:
            pending[0].continue_()
        assert saved.value.status == 200
        page.unroute('**/api/guidance')
        expect(route.locator('[data-guidance-feedback]')).to_contain_text('Saved')
        expect(route.locator('[data-guidance-version]')).to_contain_text('version 1')
        shoot(request, page, f'audit2-batch1-{kind}-{viewport}-saved')
        page.get_by_role('button', name=f'Edit the {kind}').click()
        expect(page.locator('[data-guidance-save]')).to_have_text('Save version 2')
        # An intentional deletion is also an edit worth protecting.
        text.fill('')
        page.keyboard.press('Escape')
        assert len(dialogs) == 1
        expect(text).to_have_value('')
        accepting = True
        page.locator('[data-guidance-cancel]').click()
    finally:
        page.remove_listener('dialog', handle_dialog)
        page.evaluate('fleetDeck.enterFloor(null)')
    assert changed_deck.errors == []


@pytest.mark.parametrize('view', ['building', 'world', 'workarea', 'library'])
def test_batch12_camera_keys_ignore_other_views(changed_deck: Deck, view: str) -> None:
    page = changed_deck.page
    page.locator('#viewToggle [data-view="deck"]').click()
    page.evaluate("fleetDeck.lookAtRoom('restoke', 60)")
    if view in ['building', 'world']:
        page.locator(f'#viewToggle [data-view="{view}"]').click()
    elif view == 'workarea':
        page.evaluate("async () => (await import('/js/workarea.js')).openWorkarea('restoke')")
    else:
        page.locator('#libraryOpen').click()

    def camera():
        return page.evaluate("async () => { const {cam} = await import('/js/scene.js'); return [cam.z, cam.c.toArray(), cam.userMoved]; }")

    before = camera()
    for key in ['+', '-', 'f', '=', '_', 'F']:
        page.keyboard.press(key)
        assert camera() == before, (view, key, before, camera())
    if view in ['workarea', 'library']:
        page.keyboard.press('Escape')
    else:
        page.locator('#viewToggle [data-view="deck"]').click()
    before = camera()
    page.keyboard.press('+')
    assert camera()[0] > before[0]  # The deck itself still handles zoom.
    page.evaluate('fleetDeck.lookAtRoom(null)')
    assert changed_deck.errors == []


@pytest.mark.parametrize('viewport', ['desktop', 'narrow'])
def test_batch12_idle_stale_and_control_titles(changed_deck: Deck, base_url: str,
                                             request: pytest.FixtureRequest, viewport: str) -> None:
    page = changed_deck.page
    page.set_viewport_size(VIEWPORTS[viewport])
    page.locator('#viewToggle [data-view="deck"]').click()
    doc = session_state(base_url, 0)
    page.evaluate('doc => fleetDeck.apply(doc)', doc)
    page.evaluate('key => fleetDeck.select(key)', ASKING)
    expect(page.locator('#panelHead .sess')).to_contain_text('live · idle')
    expect(page.locator('#stats .sess')).to_contain_text('waiting')
    page.locator('#panel #close').click()
    expect(page.locator('#radioToggle')).to_have_attribute('title', re.compile('Turn on.*SomaFM.*somafm.com'))
    # No live network audio in the test; verify both action states.
    page.evaluate("""() => { window.Audio = class {
        constructor() { this.dataset = {}; }
        play() { return Promise.resolve(); }
        pause() {}
    }; }""")
    page.locator('#radioToggle').click()
    expect(page.locator('#radioToggle')).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('#radioToggle')).to_have_attribute('title', re.compile('Turn off'))
    page.locator('#radioToggle').click()
    page.evaluate("async () => (await import('/js/workarea.js')).openWorkarea('restoke')")
    disabled = page.locator('[data-bench="home:a1c3e9"] [data-step="0"]')
    expect(disabled).to_be_disabled()
    expect(disabled).to_have_attribute('title', re.compile('No brief or report recorded'))
    shoot(request, page, f'batch12-{viewport}-workarea')
    page.keyboard.press('Escape')
    worker = next(host for host in doc['hosts'] if host['name'] == 'worker')
    worker.update(ok=False, error='connection lost')
    page.evaluate('doc => fleetDeck.apply(doc)', doc)
    page.evaluate("async () => (await import('/js/workarea.js')).openWorkarea('agent-fleet')")
    expect(page.locator('[data-bench="worker:f20a6d"] [data-stale]')).to_contain_text('stale')
    shoot(request, page, f'batch12-{viewport}-stale-workarea')
    page.keyboard.press('Escape')
    page.evaluate("fleetDeck.select('worker:f20a6d')")
    expect(page.locator('#panelHead [data-stale]')).to_contain_text('stale')
    shoot(request, page, f'batch12-{viewport}-stale-panel')
    page.locator('#panel #close').click()
    file_job = next(job for job in worker['jobs'] if job['id'] == 'f20a6d')
    file_job['documents'] = [{"id": f"outbox-{name}", "name": name, "path": f"/job/outbox/{name}",
                              "kind": "outbox", "media": "file", "size": size, "mtime": doc['time'], "step": None}
                             for name, size in [('data.json', 2), ('empty.csv', 0), ('scene.blend', 10)]]
    page.evaluate('doc => fleetDeck.apply(doc)', doc)
    page.evaluate("fleetDeck.select('worker:f20a6d')")
    page.locator('#panelTabs [data-tab="documents"]').click()
    expect(page.locator('#panelBody .docs .dn')).to_have_text(['scene.blend', 'empty.csv', 'data.json'])
    expect(page.locator('#panelBody .docs .go')).to_have_text(['Collect →'] * 3)
    shoot(request, page, f'batch12-{viewport}-outbox')
    page.locator('#panel #close').click()
    page.locator('#workingOpen').click()
    expect(page.locator('#runPanel .run-need')).to_contain_text('Jobs without a linked work item')
    expect(page.locator('#runPanel .run-need')).not_to_contain_text('need a work item')
    expect(page.locator('#runPanel [data-key="worker:f20a6d"] [data-stale]')).to_contain_text('stale')
    shoot(request, page, f'batch12-{viewport}-running')
    page.keyboard.press('Escape')
    page.evaluate('doc => fleetDeck.apply(doc)', session_state(base_url, 0))
    assert changed_deck.errors == []


def test_p8_keyboard_help_preserves_context_and_focus(deck: Deck, request: pytest.FixtureRequest) -> None:
    from pathlib import Path
    page = deck.page
    page.locator('#viewToggle [data-view="deck"]').click()
    page.keyboard.press('?')
    help = page.locator('#keyboardHelp')
    expect(help).to_be_visible()
    expect(help).to_contain_text('Zoom in')
    expect(help.locator('[data-help-escape]')).to_have_text('No open panel to close.')
    before = page.evaluate("async () => { const {cam}=await import('/js/scene.js'); return {z:cam.z,c:cam.c}; }")
    page.keyboard.press('+')
    assert page.evaluate("async () => { const {cam}=await import('/js/scene.js'); return {z:cam.z,c:cam.c}; }") == before
    page.keyboard.press('Tab')
    assert page.evaluate("document.activeElement.closest('#keyboardHelp') !== null")
    if request.config.getoption('--shots'):
        page.screenshot(path=str(Path(request.config.getoption('--shots')) / f'p8-{page.viewport_size["width"]}-deck.png'))
    page.keyboard.press('Escape')
    expect(help).to_be_hidden()
    page.locator('#viewToggle [data-view="building"]').click()
    page.locator('#keyboardHelpOpen').click()
    expect(help).to_contain_text('Building')
    expect(help).not_to_contain_text('Zoom in')
    page.keyboard.press('Escape')
    assert page.evaluate('document.activeElement.id') == 'keyboardHelpOpen'
    assert page.evaluate('document.body.dataset.view') == 'building'
    page.locator('#viewToggle [data-view="world"]').click()
    page.keyboard.press('?')
    expect(help.locator('[data-help-context]')).to_have_text('World')
    expect(help.locator('[data-help-escape]')).to_have_text('Frame the whole project floor.')
    page.keyboard.press('Escape')
    assert page.evaluate('document.body.dataset.view') == 'world'
    page.locator('#viewToggle [data-view="deck"]').click()
    page.locator('#libraryOpen').click()
    page.keyboard.press('?')
    expect(help.locator('[data-help-escape]')).to_have_text('Close the project library.')
    page.keyboard.press('Escape')
    expect(page.locator('#libraryPane')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('#libraryPane')).to_be_hidden()
    page.locator('.lantern[data-room="restoke"]').dispatch_event('click')
    page.keyboard.press('?')
    expect(help.locator('[data-help-escape]')).to_have_text('Close the attention list.')
    page.keyboard.press('Escape')
    expect(page.locator('#attnPanel')).to_be_visible()
    page.locator('#attnPanel [data-owner="home:e1b5c8"]').click()
    if page.viewport_size['width'] <= 760:
        expect(page.locator('#attnPanel')).to_be_hidden()
    else:
        page.locator('#attnPanel [data-close]').click()
    page.locator('#panelTabs [data-workarea]').click()
    page.keyboard.press('?')
    expect(help.locator('[data-help-escape]')).to_have_text('Close the workarea; keep the job panel open.')
    page.keyboard.press('Escape')
    expect(page.locator('#workarea')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('#panel')).to_have_class('open')
    page.locator('#panelBody [data-tab="summary"] [data-doc="report-0"]').click()
    page.keyboard.press('?')
    expect(help).to_contain_text('Previous / next document')
    expect(help.locator('[data-help-escape]')).to_have_text('Close the document reader.')
    if request.config.getoption('--shots'):
        page.screenshot(path=str(Path(request.config.getoption('--shots')) / f'p8-{page.viewport_size["width"]}-reader.png'))
    page.keyboard.press('Escape')
    expect(page.locator('#reader')).to_be_visible()
    page.keyboard.press('Escape')
    page.locator('#panel #close').click()
    # A question mark typed into an editor/search field stays text.
    page.evaluate("() => { const input=document.createElement('input'); input.id='p8-input'; document.body.append(input); input.focus(); }")
    page.keyboard.type('?')
    expect(page.locator('#p8-input')).to_have_value('?')
    expect(help).to_be_hidden()
    page.locator('#p8-input').evaluate('(el) => el.remove()')
    assert deck.errors == []


def test_p8_help_keeps_unsaved_guidance(changed_deck: Deck, deck_state, monkeypatch, tmp_path,
                                        request: pytest.FixtureRequest) -> None:
    store = open_store()
    monkeypatch.setattr(deck_state, 'store', store)
    monkeypatch.setattr(deck_state, 'attention', open_attention(store))
    repo = tmp_path / 'management'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init'], check=True, capture_output=True, timeout=10)
    open_records(store).register('restoke-v2', repo, actor='user')
    page = changed_deck.page
    page.locator('#viewToggle [data-view="building"]').click()
    page.evaluate('fleetDeck.advanceTime(0)')
    page.locator('.plate[data-floor="1"] .enter').click()
    expect(page.locator('#benchRoute')).to_have_attribute('data-level', 'floor')
    page.keyboard.press('?')
    expect(page.locator('[data-help-context]')).to_have_text('Floor')
    expect(page.locator('[data-help-escape]')).to_have_text('Return to the building.')
    page.keyboard.press('Escape')
    assert page.evaluate('document.body.dataset.view') == 'floor'
    page.locator('[data-open-constitution]').click()
    page.get_by_role('button', name='Write the constitution').click()
    text = page.locator('[data-guidance-text]')
    text.fill('Unsaved P8 draft?')
    page.locator('#libraryOpen').click()
    expect(page.locator('#libraryPane')).to_be_visible()
    page.keyboard.press('?')
    expect(page.locator('[data-help-context]')).to_have_text('Guidance editor')
    expect(page.locator('[data-help-escape]')).to_contain_text('Ask before discarding an unsaved edit')
    shoot(request, page, 'p8-guidance')
    # If Escape leaks to the editor, Playwright dismisses its confirmation and the hook records it.
    dialogs = []
    def dismiss(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()
    page.on('dialog', dismiss)
    page.keyboard.press('Escape')
    expect(text).to_have_value('Unsaved P8 draft?')
    expect(page.locator('#benchRoute')).to_have_attribute('data-editing', '')
    assert dialogs == []
    page.remove_listener('dialog', dismiss)
    page.locator('.lib-head [data-lib-close]').click()
    text.fill('')
    page.get_by_role('button', name='Discard', exact=True).click()
    page.keyboard.press('Escape')
    page.keyboard.press('Escape')
    page.locator('#viewToggle [data-view="deck"]').click()
    assert changed_deck.errors == []


def test_previous_and_next_step_through_open_attention_items(changed_deck: Deck, base_url: str) -> None:
    from fleet.projections.attention import attention_display
    page = changed_deck.page
    with urlopen(base_url + "/api/state", timeout=5) as response:
        document = json.load(response)
    model = next(row for row in document["attention"] if row["project"] == "restoke")
    document["attention"] = [row for row in document["attention"] if row["state"] != "resolved"] + [
        {**model, "id": f"step{index}", "summary": f"Question {index}", "state": "open",
         "last_seen": 1890000000 + index, "kind": "decision"} for index in range(3)]
    document["attention_display"] = attention_display(document["attention"], document["building"], document["projects"])
    page.evaluate("doc => fleetDeck.apply(doc)", document)
    # each decision's detail, as the server would answer it
    page.route("**/api/decision?**", lambda route: route.fulfill(json={
        "question": "Which way?", "context": "", "proposal": None, "options": [], "state": "open"}))
    total = len(document["attention"])
    page.locator('.lantern[data-room="restoke"]').dispatch_event("click")
    page.locator('#attnPanel .attn-item[data-id="step2"] [data-context]').first.click()   # the newest
    expect(page.locator("#rdTitle")).to_have_text("Question 2")
    expect(page.locator("#rdPos")).to_have_text(f"1 / {total} · All rooms and owners")
    expect(page.locator("#rdPrev")).to_be_disabled()
    page.locator("#rdNext").click()
    expect(page.locator("#rdTitle")).to_have_text("Question 1")
    expect(page.locator("#rdPos")).to_have_text(f"2 / {total} · All rooms and owners")
    page.keyboard.press("ArrowLeft")
    expect(page.locator("#rdTitle")).to_have_text("Question 2")
    assert changed_deck.errors == []


def test_p1_owner_fold_and_consequences(changed_deck: Deck, base_url: str, request) -> None:
    page = changed_deck.page
    with urlopen(base_url + '/api/state', timeout=5) as response:
        document = json.load(response)
    model = document['attention'][0]
    host, job = next((host, job) for host in document['hosts'] for job in host['jobs'] if job['status'] == 'running')
    document['triage'] = {'p': {'queue': ['p1-agent'], 'live_run': {
        'id': 'triage-run', 'host': host['name'], 'remote_job_id': job['id']}}}
    document['attention'] = [
        {**model, 'id': 'p1-user', 'state': 'open', 'owned_by': 'user',
         'owner_reason': 'Needs approval outside the mandate', 'delegable': False},
        {**model, 'id': 'p1-agent', 'state': 'open', 'owned_by': 'agent'}]
    from fleet.projections.attention import attention_display
    document['attention_display'] = attention_display(document['attention'], document['building'], document['projects'])
    page.evaluate('doc => fleetDeck.apply(doc)', document)
    expect(page.locator('#needYou')).to_contain_text('1 need you')
    expect(page.locator('#withAgent')).to_contain_text('1 with agent')
    page.locator('#withAgent').click()
    panel = page.locator('#attnPanel')
    expect(panel.locator('[data-id="p1-user"]')).to_contain_text('Needs approval outside the mandate')
    expect(panel.locator('[data-act="delegate"]')).to_be_disabled()
    expect(panel.locator('[data-act="delegate"]')).to_have_attribute('title', 'This project has no confirmed triage mandate')
    fold = panel.locator('[data-attention-group="agent"]')
    expect(fold).not_to_have_attribute('open', '')
    fold.locator('summary').click()
    expect(fold.locator('.attn-owned')).to_have_text('Agent')
    expect(fold.locator('[data-act="take"]')).to_have_attribute('title', 'Take this item back; revokes agent authority for this item. The triage process may continue for other items')
    expect(page.locator('#toast')).to_be_hidden(timeout=7000)
    shoot(request, page, 'p1-desktop')
    page.set_viewport_size(VIEWPORTS['narrow'])
    shoot(request, page, 'p1-390')
    page.keyboard.press('Escape')
    page.locator('#workingOpen').click()
    expect(page.locator('#runPanel .run-triage')).to_contain_text('handling 1 item')
    shoot(request, page, 'p1-running-390')
    page.keyboard.press('Escape')
    assert not changed_deck.errors


@pytest.mark.parametrize('viewport', ['desktop', 'narrow'])
def test_p2_history_archive_and_offline(changed_deck: Deck, base_url: str, request, viewport) -> None:
    """History stays store-backed, filters survive state, and archived documents use kept copies."""
    from urllib.parse import parse_qs, urlsplit
    page = changed_deck.page
    page.set_viewport_size(VIEWPORTS[viewport])
    run = {'id': 'p2-run-12345678', 'host': 'home', 'remote_job_id': 'p2-job',
           'kind': 'job', 'runtime': 'codex', 'model': 'gpt-test', 'title': 'Ship History',
           'start': page.evaluate('new Date(Date.now() - 86400000).toISOString()'), 'status': 'succeeded', 'reason': None,
           'duration_seconds': 120, 'workspace': {'branch': 'feat/history'},
           'commit_count': 1, 'push_count': 1, 'document_count': 1, 'work_item': None}
    session = {**run, 'id': 'p2-session', 'title': 'Inspect runs', 'kind': 'session',
               'start': page.evaluate('new Date(Date.now() - 172800000).toISOString()'), 'status': 'stopped', 'reason': 'quiet'}
    queries = []
    history_error = False
    def history(route):
        query = parse_qs(urlsplit(route.request.url).query)
        queries.append(query)
        if history_error:
            route.fulfill(status=503, json={'error': 'History store unavailable'})
            return
        runs = [] if query.get('host') == ['missing'] else [run, session]
        route.fulfill(json={'runs': runs, 'total': 102 if runs else 0, 'limit': int(query['limit'][0]),
                            'empty_reason': 'No stored runs match these filters.' if not runs else None})
    detail = {'run': run, 'steps': [{'index': 0, 'title': 'Ship', 'status': 'done',
               'git': {'commit_count': 1, 'commits': [{'sha': 'abcdef123456', 'subject': 'Add history'}],
                       'pushes': [{'ref': 'origin/main'}]}},
               {'index': 1, 'title': 'Verify', 'status': 'done',
                'git': {'commit_count': 0, 'commits': [], 'pushes': []}}], 'documents': [],
              'kept_documents': [{'id': 'report-0', 'name': 'History report', 'kind': 'report',
                                  'stored': True, 'scope': 'p2', 'job_key': 'home-p2-job'}],
              'trace': {'events': {'availability': 'kept', 'content': json.dumps({'kind': 'result', 'summary': 'History shipped'})},
                        'source': {'availability': 'removed by fleet rm', 'raw': []}}}
    page.route('**/api/history/runs?*', history)
    page.route('**/api/runs/p2-run-12345678', lambda route: route.fulfill(json=detail))
    page.route('**/api/library/job?*', lambda route: route.fulfill(json={
        'id': 'report-0', 'name': 'History report', 'kind': 'report', 'html': '<p>Kept report body</p>',
        'markdown': 'Kept report body', 'toc': [], 'words': 3, 'minutes': 1}))
    bench_changed = False
    page.route('**/api/bench?*', lambda route: route.fulfill(json={'rooms': [{'id': 'changed-room'}] if bench_changed else []}))
    try:
        page.evaluate("fleetDeck.enterFloor('p2')")
        page.locator('[data-open-history]').click()
        expect(page.locator('[data-history-run]')).to_have_count(2)
        expect(page.locator('[data-history-day]')).to_have_count(2)
        expect(page.locator('[data-run-history]')).to_contain_text('2 of 102 stored runs')
        expect(page.locator('[data-run-history]')).to_contain_text('quiet')
        shoot(request, page, f'p2-history-{viewport}')
        page.locator('[data-history-more]').click()
        expect(page.locator('[data-history-run]')).to_have_count(2)
        assert queries[-1]['limit'] == ['200']
        page.locator('[data-history-filters] [name=kind]').select_option('job')
        page.locator('[data-history-filters] [name=status]').select_option('succeeded')
        page.locator('[data-history-filters] [name=since]').fill('7d')
        page.locator('[data-history-filters] [name=unlinked]').check()
        page.locator('[data-history-filters] button[type=submit]').click()
        expect(page.locator('[data-history-run]')).to_have_count(2)
        assert queries[-1]['kind'] == ['job'] and queries[-1]['status'] == ['succeeded']
        assert queries[-1]['since'] == ['7d'] and queries[-1]['unlinked'] == ['true']
        with urlopen(base_url + '/api/state', timeout=5) as response:
            state = json.load(response)
        bench_changed = True
        page.evaluate('doc => fleetDeck.apply(doc)', state)
        page.evaluate("async () => { advanceClock(4); const bench = await import('/js/bench.js'); await bench.refreshBench(); }")
        expect(page.locator('[data-history-filters] [name=since]')).to_have_value('7d')
        history_error = True
        page.locator('[data-history-filters] button[type=submit]').click()
        expect(page.locator('[data-history-results] [role=alert]')).to_have_text('History store unavailable')
        history_error = False
        page.locator('[data-history-retry]').click()
        expect(page.locator('[data-history-run]')).to_have_count(2)

        page.locator('[data-history-filters] [name=host]').fill('missing')
        page.locator('[data-history-filters] button[type=submit]').click()
        expect(page.locator('[data-history-empty]')).to_contain_text('host: missing')
        shoot(request, page, f'p2-empty-{viewport}')
        page.locator('[data-history-filters] button[type=reset]').click()
        page.locator('[data-open-run="p2-run-12345678"]').click()
        expect(page.locator('#panel')).to_have_attribute('data-archived', '')
        expect(page.locator('[data-archived-content]')).to_contain_text('Add history')
        expect(page.locator('[data-archived-content]')).to_contain_text('origin/main')
        expect(page.locator('[data-archived-content]')).to_contain_text('No commits in this step')
        shoot(request, page, f'p2-archive-{viewport}')
        page.evaluate('doc => fleetDeck.apply(doc)', state)
        expect(page.locator('[data-archived-content]')).to_contain_text('Add history')

        page.locator('#panelTabs [data-tab=activity]').click()
        expect(page.locator('[data-archived-content]')).to_contain_text('History shipped')
        expect(page.locator('[data-archived-content]')).to_contain_text('removed by fleet rm')
        page.locator('#panelTabs [data-tab=documents]').click()
        page.locator('[data-kept-doc]').click()
        expect(page.locator('#rdBody')).to_contain_text('Kept report body')
        page.keyboard.press('Escape')
        page.keyboard.press('Escape')
        expect(page.locator('#panel')).to_have_attribute('aria-hidden', 'true')
        expect(page.locator('[data-run-history]')).to_be_visible()
        page.evaluate('fleetDeck.enterFloor(null)')
        with urlopen(base_url + '/api/state', timeout=5) as response:
            document = json.load(response)
        host = next(h for h in document['hosts'] if any(j['status'] == 'running' for j in h['jobs']))
        host.update(ok=False, error='test offline', down_since=page.evaluate('Date.now() / 1000 - 7200'))
        page.evaluate('doc => fleetDeck.apply(doc)', document)
        expect(page.locator('.tag[data-stale]').first).to_have_attribute('title', re.compile('offline since'))
        shoot(request, page, f'p2-offline-androids-{viewport}')
        page.locator('#workingOpen').click()
        expect(page.locator('#runPanel [data-stale]').first).to_contain_text('offline since')
        shoot(request, page, f'p2-offline-{viewport}')
        changed_deck.errors[:] = [error for error in changed_deck.errors if '503 (Service Unavailable)' not in error]
        assert queries[0]['project'] == ['p2'] and queries[0]['limit'] == ['100']
        assert not changed_deck.errors
    finally:
        page.unroute('**/api/history/runs?*', history)
        page.unroute('**/api/runs/p2-run-12345678')
        page.unroute('**/api/library/job?*')
        page.unroute('**/api/bench?*')
        page.evaluate('fleetDeck.enterFloor(null)')


@pytest.mark.parametrize('viewport', ['desktop', 'narrow'])
def test_p3_item_history_and_work_runs(changed_deck: Deck, base_url: str, request, viewport) -> None:
    from urllib.parse import parse_qs, urlsplit
    page = changed_deck.page
    page.set_viewport_size(VIEWPORTS[viewport])
    subjects = []
    audit_mode = 'populated'
    def audit(route):
        subject = parse_qs(urlsplit(route.request.url).query)['subject'][0]
        subjects.append(subject)
        if audit_mode == 'error':
            route.fulfill(status=503, json={'error': 'Audit store unavailable'})
            return
        if audit_mode == 'empty':
            route.fulfill(json={'entries': []})
            return
        kind = 'attention' if subject.startswith('attention:') else 'run' if subject.startswith('execution:') else 'work item'
        change = {'field': 'owned_by', 'before': 'user', 'after': 'agent'} if kind == 'attention' else {'field': 'status', 'before': 'running', 'after': 'succeeded'} if kind == 'run' else {'field': 'next_step', 'before': 'Inspect', 'after': 'Ship'}
        route.fulfill(json={'entries': [
            {'sequence': 1, 'time': '2026-09-25T10:00:00Z', 'actor': 'user', 'kind': kind,
             'changes': [{'field': 'title', 'before': None, 'after': '<Initial title>'}], 'source_run': None, 'job': None},
            {'sequence': 2, 'time': '2026-09-26T10:00:00Z', 'actor': 'triage-agent', 'kind': kind,
             'changes': [change], 'source_run': 'p3-run', 'job': 'home:p3-job'}]})
    run = {'id': 'p3-run', 'title': 'Audit change', 'host': 'home', 'remote_job_id': 'p3-job',
           'kind': 'job', 'status': 'succeeded', 'start': None, 'workspace': None, 'work_item': None}
    run_queries = []
    def runs(route):
        run_queries.append(parse_qs(urlsplit(route.request.url).query))
        route.fulfill(json={'runs': [run], 'total': 1, 'limit': 100})
    page.route('**/api/history?*', audit)
    page.route('**/api/history/runs?*', runs)
    page.route('**/api/runs/p3-run', lambda route: route.fulfill(json={
        'run': run, 'steps': [], 'kept_documents': [], 'documents': [], 'trace': {}}))
    page.route('**/api/bench?*', lambda route: route.fulfill(json={'rooms': []}))
    try:
        with urlopen(base_url + '/api/state', timeout=5) as response:
            doc = json.load(response)
        page.evaluate('doc => fleetDeck.apply(doc)', doc)
        page.locator('#needYou').click()
        first = page.locator('#attnPanel [data-item-attention-history]').first
        attention_id = first.get_attribute('data-item-attention-history')
        first.click()
        sheet = page.locator('#itemHistoryPanel')
        expect(sheet.locator('[data-audit-sequence]').first).to_have_attribute('data-audit-sequence', '2')
        expect(sheet).to_contain_text('triage-agent')
        expect(sheet.locator('[data-before]').first).to_have_text('user')
        expect(sheet.locator('[data-after]').first).to_have_text('agent')
        expect(sheet).to_contain_text('<Initial title>')
        assert subjects[-1] == f'attention:{attention_id}'
        shoot(request, page, f'p3-attention-{viewport}')
        audit_mode = 'empty'
        sheet.locator('[data-audit-refresh]').click()
        expect(sheet.locator('[data-audit-empty]')).to_have_text('No stored changes for this item.')
        shoot(request, page, f'p3-empty-{viewport}')
        audit_mode = 'error'
        sheet.locator('[data-audit-refresh]').click()
        expect(sheet.locator('[data-audit-results] [role=alert]')).to_have_text('Audit store unavailable')
        audit_mode = 'populated'
        sheet.locator('[data-audit-retry]').click()
        expect(sheet.locator('[data-audit-sequence]')).to_have_count(2)

        sheet.locator('[data-item-tab=details]').click()
        expect(sheet.locator('[data-item-details]')).to_be_visible()
        sheet.locator('[data-item-tab=history]').click()
        page.keyboard.press('Escape')
        expect(sheet).not_to_have_attribute('open', '')
        page.keyboard.press('Escape')
        # Open the shared work-item sheet with the same object shape the plan rows supply.
        page.evaluate("async () => { const m = await import('/js/item-history.js'); m.openItemHistory({id:'p3-work', title:'Audit work', kind:'work item', project:'p3'}); }")
        expect(sheet.locator('[data-history-run]')).to_have_count(1)
        assert run_queries[-1]['work_item'] == ['p3-work']
        expect(sheet.locator('[name=work_item]')).to_have_attribute('readonly', '')
        assert subjects[-1] == 'work:item:p3-work'
        shoot(request, page, f'p3-work-{viewport}')
        sheet.locator('[data-item-runs] [data-copy]').click()
        expect(sheet.locator('[data-item-runs] [data-copy]')).to_have_text('Copied')
        sheet.locator('[data-item-runs] [data-history-run]').scroll_into_view_if_needed()
        shoot(request, page, f'p3-work-runs-{viewport}')
        sheet.locator('[data-open-run]').click()
        expect(sheet).not_to_have_attribute('open', '')
        page.locator('#panelTabs [data-tab=history]').click()
        expect(page.locator('#panel [data-audit-sequence]').first).to_have_attribute('data-audit-sequence', '2')
        assert subjects[-1] == 'execution:run:p3-run'
        shoot(request, page, f'p3-run-{viewport}')
        page.locator('#panel #close').click()
        host = next(h for h in doc['hosts'] if h['jobs'])
        job = host['jobs'][0]
        job['audit_run_id'] = 'p3-run'
        page.evaluate('doc => fleetDeck.apply(doc)', doc)
        page.evaluate('key => fleetDeck.select(key)', host['name'] + ':' + job['id'])
        page.locator('#panelTabs [data-tab=history]').click()
        expect(page.locator('#panel [data-audit-sequence]')).to_have_count(2)
        assert subjects[-1] == 'execution:run:p3-run'
        page.locator('#panelTabs [data-tab=summary]').click()
        page.locator('#panel #close').click()
        changed_deck.errors[:] = [error for error in changed_deck.errors if '503 (Service Unavailable)' not in error]
        assert not changed_deck.errors
    finally:
        page.unroute('**/api/history?*')
        page.unroute('**/api/history/runs?*')
        page.unroute('**/api/runs/p3-run')
        page.unroute('**/api/bench?*')
        page.locator('#itemHistoryPanel').evaluate('el => el.close()')


def test_p3_work_entrypoint_reads_real_audit(changed_deck: Deck, route_migration, request) -> None:
    page = changed_deck.page
    root = route_migration['routes']
    page.evaluate("fleetDeck.enterFloor('restoke-v2')")
    page.locator(f'[data-epic-card="{root.id}"] [data-work-history]').click()
    sheet = page.locator('#itemHistoryPanel')
    expect(sheet.locator('[data-item-audit]')).to_have_attribute('data-audit-subject', f'work:item:{root.id}')
    expect(sheet.locator('[data-audit-sequence]')).not_to_have_count(0)
    expect(sheet.locator('[data-item-audit]')).to_contain_text('user')
    expect(sheet.locator('[data-item-runs]')).to_contain_text('stored runs')
    shoot(request, page, 'p3-real-work-desktop')
    page.keyboard.press('Escape')
    page.evaluate('fleetDeck.enterFloor(null)')
    assert not changed_deck.errors

@pytest.mark.parametrize('viewport', ['desktop', 'narrow'])
def test_triage_policy_room(changed_deck: Deck, route_migration, request, viewport):
    from fleet import composition
    from fleet.modules.records import TRIAGE_PATH
    page = changed_deck.page
    page.set_viewport_size({'width': 390 if viewport == 'narrow' else 1440, 'height': 844 if viewport == 'narrow' else 900})
    body = dict(goal='Triage within policy', constraints=['No deployment'], escalation_conditions=['Outside mandate'],
                criteria_it_may_judge=[], decision_authority=['retry', 'escalate'], host='home', runtime='codex',
                cwd='/workspace', permission='acceptEdits', routing={'failed': 'agent'},
                permissions={'allow': ['Read'], 'escalate': ['Bash(git push:*)']},
                limits={'retries_per_step': 2, 'runs_per_day': 12, 'unclaimed_minutes': 30})
    composition.open_records().write_mandate('restoke-v2', TRIAGE_PATH, json.dumps(body), key='policy-browser', actor='policy-author')
    page.evaluate("fleetDeck.enterFloor('restoke-v2')")
    page.locator('[data-epic-card] button[data-epic]').first.click()
    policy = page.locator('[data-room-policy] [data-triage-policy]')
    expect(policy).to_contain_text('version 1')
    expect(policy).to_contain_text('policy-author')
    expect(policy).to_contain_text('Bash(git push:*)')
    expect(policy).to_contain_text('cannot complete work or judge criteria')
    policy.scroll_into_view_if_needed()
    shoot(request, page, f'triage-policy-{viewport}')
    page.locator('[data-room-policy] [data-open-constitution]').click()
    expect(page.locator('[data-constitution-page] [data-triage-policy]')).to_contain_text('version 1')


@pytest.mark.parametrize('viewport', VIEWPORTS)
def test_audit2_batch1_reader_drafts_and_legacy_session(changed_deck: Deck, request, viewport) -> None:
    page = changed_deck.page
    page.set_viewport_size(VIEWPORTS[viewport])
    page.route('**/api/decision?*', lambda route: route.fulfill(json={
        'question': 'Which route?', 'context': 'Review', 'options': ['Direct', 'Scenic'], 'proposal': None}))
    items = [dict(project='restoke-v2', owned_by='user', owner=None, age=0, id=f'a2-first-{viewport}', kind='decision', summary='Which route?', source='manual',
                  source_reference='review', context_reference='Review', state='open', last_seen=200),
             dict(project='other', owned_by='agent', owner=None, age=0, id=f'a2-second-{viewport}', kind='decision', summary='Another room', source='manual',
                  source_reference='other', context_reference='Other', state='open', last_seen=100)]
    page.evaluate("""async items => {
        const doc = await (await fetch('/api/state')).json();
        doc.attention.push(...items); fleetDeck.apply(doc);
        (await import('/js/reader.js')).openAttentionReader(items[0]);
    }""", items)
    answer = page.get_by_label('Your answer', exact=True)
    answer.fill('Keep this typed answer')
    page.get_by_role('radio', name='Scenic', exact=True).check()
    page.locator('#rdNext').click()
    expect(answer).to_have_value('')
    answer.fill('Separate second answer')
    page.locator('#rdPrev').click()
    expect(answer).to_have_value('Keep this typed answer')
    expect(page.locator('#rdNext')).to_have_attribute('title', re.compile('all rooms and owners'))
    expect(page.get_by_role('radio', name='Scenic', exact=True)).to_be_checked()
    close_box = page.get_by_role('button', name='Close reader', exact=True).bounding_box()
    assert close_box['x'] >= 0 and close_box['x'] + close_box['width'] <= VIEWPORTS[viewport]['width'], close_box
    shoot(request, page, f'audit2-batch1-reader-{viewport}')
    page.locator('#rdNext').click()
    expect(answer).to_have_value('Separate second answer')
    legacy = dict(items[0], id='a2-session', source='stream:home',
                  source_reference='session:home:session-id:question', context_reference='session:home:session-id')
    page.evaluate("async item => (await import('/js/reader.js')).openAttentionReader(item)", legacy)
    expect(page.locator('#rdBody')).to_contain_text('session’s terminal')
    expect(page.locator('#rdBody form')).to_have_count(0)
    expect(page.locator('#rdBody')).to_contain_text('session:home:session-id')
    shoot(request, page, f'audit2-batch1-session-{viewport}')
    page.unroute('**/api/decision?*')


@pytest.mark.parametrize('viewport', VIEWPORTS)
def test_audit2_batch2_resolved_context_and_real_history(changed_deck: Deck, deck_state, monkeypatch,
                                                       request, viewport) -> None:
    from fleet.projections.attention import attention_display, attention_items
    store = open_store()
    attention = open_attention(store)
    monkeypatch.setattr(deck_state, 'store', store)
    monkeypatch.setattr(deck_state, 'attention', attention)
    item = attention.raise_item(project='restoke', kind='decision', owner='user', source='manual',
        source_reference='batch2-resolved', headline='Which recovery?', context_reference='report:failure', actor='reporter')
    attention.delegate(item.id, actor='adi', note='Review under the charter')
    attention.take(item.id, actor='supervisor', reason='Choose recovery in person')
    attention.resolve(item.id, details='Recovery discussed in the terminal', actor='adi')
    page = changed_deck.page
    page.set_viewport_size(VIEWPORTS[viewport])
    document = deck_state.document()
    document['attention'] = attention_items(attention, document['hosts'])
    document['attention_display'] = attention_display(document['attention'], document['building'], document['projects'])
    page.evaluate('doc => fleetDeck.apply(doc)', document)
    page.locator('#needYou').click()
    panel = page.locator('#attnPanel')
    expect(panel.locator('.attn-empty')).to_contain_text('No open attention')
    fold = panel.locator('[data-attention-group=resolved]')
    expect(fold.locator('summary')).to_have_text('Resolved · 1')
    expect(fold).not_to_have_attribute('open', '')
    fold.locator('summary').click()
    row = fold.locator(f'[data-id="{item.id}"]')
    expect(row).to_contain_text('Recovery discussed in the terminal')
    expect(row).to_contain_text('Resolved at')
    expect(row.locator('[data-act], [data-owner]')).to_have_count(0)
    before = store.latest_sequence()
    row.get_by_role('button', name='History', exact=True).click()
    history = page.locator('#itemHistoryPanel')
    expect(history.locator('[data-audit-results]')).to_contain_text('Choose recovery in person')
    expect(history.locator('[data-audit-results]')).to_contain_text('Review under the charter')
    expect(history.locator('.audit-entries li')).to_have_count(4)
    close_box = page.get_by_role('button', name='Close item history').bounding_box()
    assert close_box['x'] >= 0 and close_box['x'] + close_box['width'] <= VIEWPORTS[viewport]['width'], close_box
    shoot(request, page, f'audit2-batch2-history-{viewport}')
    page.get_by_role('button', name='Close item history').click()
    expect(fold).to_have_attribute('open', '')
    row.get_by_role('button', name='Read context', exact=True).click()
    expect(page.locator('#rdBody')).to_contain_text('Recovery discussed in the terminal')
    expect(page.locator('#rdBody')).to_contain_text('Read-only')
    expect(page.locator('#rdBody form, #rdBody [data-dismiss], #rdBody [data-scope]')).to_have_count(0)
    page.get_by_role('button', name='Close reader', exact=True).click()
    shoot(request, page, f'audit2-batch2-resolved-{viewport}')
    fold.locator('summary').click()
    expect(fold).not_to_have_attribute('open', '')
    assert store.latest_sequence() == before
    page.locator('#attnPanel [data-close]').click()
    document['attention'] = []
    document['attention_display'] = attention_display([], document['building'], document['projects'])
    page.evaluate('doc => fleetDeck.apply(doc)', document)
    page.locator('#needYou').click()
    expect(panel.locator('.attn-empty')).to_contain_text('No open attention')
    expect(panel.locator('[data-attention-group=resolved]')).to_have_count(0)
    shoot(request, page, f'audit2-batch2-empty-{viewport}')
    page.locator('#attnPanel [data-close]').click()


@pytest.mark.parametrize('viewport', VIEWPORTS)
def test_audit2_batch2_feed_availability(changed_deck: Deck, request, viewport) -> None:
    page = changed_deck.page
    page.set_viewport_size(VIEWPORTS[viewport])
    page.evaluate("""async viewport => {
        const { feed } = await import('/js/model.js');
        feed.unshift({host: 'home', id: 'absent-batch2-' + viewport, label: 'absent', live: false,
                      ev: {kind: 'tool', tool: 'bash', summary: 'Retained absent subject activity', ts: 200}});
        (await import('/js/panel.js')).renderFeed();
    }""", viewport)
    absent = page.locator(f'#feedList li[data-key="home:absent-batch2-{viewport}"]')
    expect(absent).to_contain_text('Subject unavailable')
    expect(absent).to_have_attribute('title', re.compile('not available in the current deck'))
    expect(absent.locator('button')).to_have_count(0)
    assert absent.locator('.s').bounding_box()['width'] > 30
    absent.scroll_into_view_if_needed()
    shoot(request, page, f'audit2-batch2-feed-{viewport}')
    live = page.locator('#feedList li[data-key="home:a1c3e9"] button').first
    expect(live).to_have_attribute('title', re.compile('Open job'))
    live.click()
    expect(page.locator('#panel')).to_have_class('open')
    page.locator('#panel #close').click()
    page.evaluate("""async viewport => {
        const { feed } = await import('/js/model.js');
        const index = feed.findIndex(f => f.id === 'absent-batch2-' + viewport);
        feed.splice(index, 1);
        (await import('/js/panel.js')).renderFeed();
    }""", viewport)


@pytest.mark.parametrize('viewport', VIEWPORTS)
def test_audit2_batch2_missing_workarea(changed_deck: Deck, request, viewport) -> None:
    page = changed_deck.page
    page.set_viewport_size(VIEWPORTS[viewport])
    page.evaluate("""async () => {
        const { workOf } = await import('/js/model.js');
        const job = workOf('home:a1c3e9').job;
        job.project = null;
        fleetDeck.select('home:a1c3e9');
    }""")
    workarea = page.locator('#panel [data-workarea]')
    expect(workarea).to_be_disabled()
    expect(workarea).to_have_attribute('title', 'No project label reported; no room workarea is available')
    shoot(request, page, f'audit2-batch2-workarea-{viewport}')
    page.locator('#panel #close').click()

@pytest.mark.parametrize('viewport', ['desktop', 'narrow'])
def test_a2_batch3_triage_diagnostics(changed_deck: Deck, base_url: str, request, viewport):
    from fleet.projections.attention import attention_display
    page = changed_deck.page
    page.set_viewport_size(VIEWPORTS[viewport])
    with urlopen(base_url + '/api/state', timeout=5) as response:
        document = json.load(response)
    host, job = next((host, job) for host in document['hosts'] for job in host['jobs'] if job['status'] == 'running')
    model = document['attention'][0]
    document['attention'] = [{**model, 'id': 'a2-agent', 'project_id': 'p', 'owned_by': 'agent', 'state': 'open'}]
    document['triage'] = {'p': {'queue': ['a2-agent'], 'mandate_version': 'v1', 'budget_left': 0,
        'budget_resets_at': '2026-10-03T00:00:00+00:00', 'oldest_wait_seconds': 1860,
        'delivery_error': 'connection refused', 'pending_publications': [{'id': 'publication'}],
        'live_run': {'id': 'triage-run', 'status': 'unknown outcome', 'host': host['name'], 'remote_job_id': job['id']}}}
    document['attention_display'] = attention_display(document['attention'], document['building'], document['projects'])
    page.evaluate('doc => fleetDeck.apply(doc)', document)
    page.locator('#withAgent').click()
    panel = page.locator('#attnPanel')
    expect(panel).to_contain_text('1 queued')
    expect(panel).to_contain_text('oldest wait 31 min')
    expect(panel).to_contain_text('0 runs left')
    expect(panel).to_contain_text('connection refused')
    expect(panel).to_contain_text('1 pending publications')
    expect(panel).to_contain_text('unknown outcome')
    panel.locator('[data-attention-group="agent"] summary').click()
    expect(panel.locator('[data-act="take"]')).to_have_attribute('title', 'Take this item back; revokes agent authority for this item. The triage process may continue for other items')
    shoot(request, page, f'a2-batch3-health-{viewport}')
    page.keyboard.press('Escape')
    page.locator('#workingOpen').click()
    expect(page.locator('#runPanel .run-triage')).to_contain_text('connection refused')
    expect(page.locator('#runPanel .run-triage')).to_contain_text('0 runs left')
    shoot(request, page, f'a2-batch3-running-{viewport}')
    page.keyboard.press('Escape')
    document['triage']['p'].update(mandate_version=None, budget_left=None, budget_resets_at=None, live_run=None)
    page.evaluate('doc => fleetDeck.apply(doc)', document)
    page.locator('#withAgent').click()
    expect(panel).to_contain_text('No confirmed triage policy; agent-owned items cannot be serviced')
    shoot(request, page, f'a2-batch3-no-policy-{viewport}')
    assert not changed_deck.errors


@pytest.mark.parametrize('viewport', ['desktop', 'narrow'])
def test_a2_batch4_world_hit_consequences(changed_deck: Deck, base_url: str, request, viewport):
    page = changed_deck.page
    page.set_viewport_size(VIEWPORTS[viewport])
    try:
        page.locator('#viewToggle [data-view="world"]').click()
        page.wait_for_function('window.fleetWorld && fleetWorld.ready', timeout=60000)
        page.locator('#floorWorldProject').select_option('restoke')
        page.evaluate("fleetWorld.frame('near')")
        page.wait_for_function('!fleetWorld.engine.camera.moving')
        key = page.evaluate('fleetWorld.state().members[0].key')
        x, y = page.evaluate('key => fleetWorld.robotAt(key)', key)
        page.mouse.move(x, y)
        tip = page.locator('#floorWorldUi .shelf-tip')
        expect(tip).to_contain_text('Open job')
        expect(page.locator('#floorWorldCanvas')).to_have_class(re.compile(r'\bhot\b'))
        shoot(request, page, f'a2-batch4-world-robot-{viewport}')
        page.mouse.click(x, y)
        expect(page.locator('#panel')).to_have_class(re.compile(r'\bopen\b'))
        page.keyboard.press('Escape')
        # The plan wall is outside the close bench frame; reveal the whole floor before picking it.
        page.evaluate("fleetWorld.frame('far')")
        page.wait_for_function('!fleetWorld.engine.camera.moving')
        # Use actual picked pixels to cover benches, plan walls and step tiles, rather than synthetic hit objects.
        points = page.evaluate('''() => {
          const world = fleetWorld.engine, points = {};
          for (let y = 45; y < innerHeight - 60; y += 2) for (let x = 5; x < innerWidth - 5; x += 2) {
            const hit = world.pick(x, y), type = hit?.place?.split(':')[0];
            if (['bench', 'plan', 'step'].includes(type) && !points[type]) points[type] = [x, y];
          }
          return points;
        }''')
        assert 'bench' in points and 'plan' in points and 'step' in points
        for kind, (x, y) in points.items():
            page.mouse.move(x, y)
            expect(tip).to_contain_text('Zoom to this bench')
            expect(tip).to_contain_text('Esc zooms out')
            expect(page.locator('#floorWorldCanvas')).to_have_class(re.compile(r'\bhot\b'))
        shoot(request, page, f'a2-batch4-world-bench-{viewport}')
        page.mouse.click(*points['bench'])
        page.wait_for_function('fleetWorld.engine.camera.zoomLevel() > 0.5')
        page.keyboard.press('Escape')
        page.wait_for_function('fleetWorld.engine.camera.zoomLevel() < 0.01')
        page.locator('#floorWorldHelp summary').click()
        expect(page.locator('#floorWorldHelp p')).to_be_visible()
        help_box = page.locator('#floorWorldHelp p').bounding_box()
        assert help_box['x'] >= 0 and help_box['x'] + help_box['width'] <= page.viewport_size['width']
        expect(page.locator('#floorWorldHelp')).to_contain_text('Tap a robot or lantern to open its job')
        expect(page.locator('#floorWorldHelp')).to_contain_text('Pinch to zoom out')
        shoot(request, page, f'a2-batch4-world-controls-{viewport}')
        page.locator('#floorWorldHelp summary').click()
        assert not changed_deck.errors
    finally:
        page.locator('#viewToggle [data-view="deck"]').click()


# Actual HTTP History data, with fixture deck geometry/UI: prefixes and status labels are not mocked.
from tests.test_run_history import history, api


@pytest.mark.parametrize('viewport', ['desktop', 'narrow'])
def test_a2_batch4_history_status_and_http_prefixes(changed_deck: Deck, history, api, request, viewport):
    from urllib.parse import urlsplit
    from urllib.error import HTTPError
    from dataclasses import replace
    from fleet.modules.execution import JobObservation
    page = changed_deck.page
    page.set_viewport_size(VIEWPORTS[viewport])
    execution, work, root, run, now = history[3], history[2], history[4], history[6], history[-1]
    with work.repository.transaction() as repository:
        for suffix in ['one', 'two']:
            repository.save('item', replace(root, id='audit-prefix-' + suffix), 'test')
    queued = execution.record_observed('carbon', {'id': 'a2-queued', 'description': 'Waiting for worker'}, root.project)
    execution.link('carbon', 'a2-queued', root.id, actor='test')
    execution.observe('carbon', JobObservation('a2-queued', 'queued', 'codex', None, None, now))
    execution.observe(run.host, JobObservation(run.remote_job_id, 'stalled', 'codex', run.start, None, now))
    execution.record_host('carbon', reachable=False, error='connection refused')
    def proxy(route):
        parsed = urlsplit(route.request.url)
        try:
            with urlopen(api + parsed.path + ('?' + parsed.query if parsed.query else ''), timeout=5) as response:
                route.fulfill(status=response.status, content_type='application/json', body=response.read())
        except HTTPError as error:
            route.fulfill(status=error.code, content_type='application/json', body=error.read())
    page.route('**/api/history/runs?*', proxy)
    page.route('**/api/runs/*', proxy)
    page.route('**/api/bench?*', lambda route: route.fulfill(json={'rooms': []}))
    try:
        page.evaluate('project => fleetDeck.enterFloor(project)', root.project)
        page.locator('[data-open-history]').click()
        history_view = page.locator('[data-run-history]')
        expect(history_view).to_contain_text('queued (not started)')
        expect(history_view).to_contain_text('stalled (outcome unknown)')
        expect(history_view).to_contain_text('offline since')
        field = page.locator('[data-history-filters] [name=work_item]')
        apply = page.locator('[data-history-filters] button[type=submit]')
        field.fill(root.id)
        apply.click()
        expect(history_view).to_contain_text('2 of 2 stored runs')
        full = page.locator('[data-history-run]').evaluate_all('rows => rows.map(row => row.dataset.historyRun)')
        field.fill(root.id[:8])
        apply.click()
        expect(history_view).to_contain_text('2 of 2 stored runs')
        assert page.locator('[data-history-run]').evaluate_all('rows => rows.map(row => row.dataset.historyRun)') == full
        shoot(request, page, f'a2-batch4-history-{viewport}')
        history_view.locator(f'[data-open-run="{queued.id}"]').click()
        expect(page.locator('#panelBody')).to_contain_text('queued (not started)')
        expect(page.locator('#panelBody')).to_contain_text('last known worker state')
        shoot(request, page, f'a2-batch4-queued-archive-{viewport}')
        page.keyboard.press('Escape')
        assert not changed_deck.errors
        field.fill('audit-prefix-')
        apply.click()
        expect(history_view.locator('[role=alert]')).to_contain_text('ambiguous work item')
        expect(history_view).to_contain_text('audit-prefix-one')
        expect(history_view).to_contain_text('audit-prefix-two')
        shoot(request, page, f'a2-batch4-prefix-ambiguity-{viewport}')
        assert changed_deck.errors == ['Failed to load resource: the server responded with a status of 400 (Bad Request)']
    finally:
        page.unroute('**/api/history/runs?*', proxy)
        page.unroute('**/api/runs/*', proxy)
        page.unroute('**/api/bench?*')
        page.locator('#viewToggle [data-view="deck"]').click()
