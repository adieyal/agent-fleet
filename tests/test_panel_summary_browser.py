"""The agent panel: a Summary of what the agent is doing by default, the raw trace in the Activity tab."""

import copy
import json
from collections.abc import Iterator
from typing import Any
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, Page, expect

from test_deck_browser import PIN_CLOCK, VIEWPORTS, on_the_floor

RUNNING = "home:a1c3e9"                                   # step 2 of 2, a todo list with one item in progress
ASKING = "home:8e1f0c42-2b7d-4a55-9c1e-7f3a2d6b9e10"     # an idle session with a question waiting
FAILED = "home:e1b5c8"                                    # a failed step with its report
SAID = "Every job now gets the writing guide in its preamble, so reports come out illustrated. Next I check the reader."


def tool(tool: str, name: str, summary: str, ts: int) -> dict[str, Any]:
    return {"kind": "tool", "tool": tool, "name": name, "summary": summary, "ts": ts}


def trace(t: int) -> list[dict[str, Any]]:
    """Step 2 starts with tools only, then two narrations, each followed by its tools; a thought in between."""
    events = [{"kind": "step", "step": 1, "status": "running", "summary": "Build the V2 route behind the feature flag", "ts": t}]
    events += [tool("bash", "Bash", f"pnpm vitest run part{i}", t + 1 + i) for i in range(4)]
    events += [tool("edit", "Edit", f"frontend/src/v2/part{i}.tsx", t + 5 + i) for i in range(2)]
    events += [{"kind": "text", "summary": "Reading the legacy drawer first. It has fourteen behaviours.", "ts": t + 10},
               tool("think", "", "thinking…", t + 11),
               tool("read", "Read", "frontend/src/routes/suppliers/SupplierDrawer.tsx", t + 12)]
    events += [tool("edit", "Edit", f"frontend/src/v2/drawer{i}.tsx", t + 13 + i) for i in range(3)]
    events += [tool("bash", "Bash", f"pnpm tsc --noEmit -p {i}", t + 16 + i) for i in range(2)]
    events += [{"kind": "text", "summary": SAID, "ts": t + 20}]
    return events


def state(base_url: str) -> dict[str, Any]:
    with urlopen(base_url + "/api/state", timeout=5) as response:
        return json.load(response)


def with_trace(doc: dict[str, Any], key: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    doc = copy.deepcopy(doc)
    host, job_id = key.split(":", 1)
    for h in doc["hosts"]:
        for job in h["jobs"] + h["sessions"]:
            if h["name"] == host and job["id"] == job_id:
                job["events"] = events
    return doc


@pytest.fixture(scope="module")
def page(browser: Browser, base_url: str, fixture_data: dict[str, Any]) -> Iterator[Page]:
    context = browser.new_context(viewport=VIEWPORTS["desktop"], reduced_motion="reduce")
    context.add_init_script(PIN_CLOCK % (fixture_data["time"], fixture_data["time"]))
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/")
    page.wait_for_function(f"window.fleetDeck && (fleetDeck.advanceTime(0), fleetDeck.agents().length === {len(on_the_floor(fixture_data))})")
    yield page
    assert errors == []
    context.close()


@pytest.fixture
def deck(page: Page, base_url: str) -> Iterator[Page]:
    """The shared page with the Summary chosen and nothing open; afterwards the server's state again."""
    original = state(base_url)
    page.evaluate("localStorage.removeItem('fleet.panel.tab')")
    yield page
    page.keyboard.press("Escape")
    page.evaluate("doc => { fleetDeck.apply(doc); localStorage.removeItem('fleet.panel.tab'); }", original)
    if page.locator("#panel.open").count():
        page.locator("#panel #close").click()


def open_panel(page: Page, key: str) -> None:
    page.evaluate("key => fleetDeck.select(key)", key)
    expect(page.locator("#panel")).to_have_class("open")


def summary(page: Page):
    return page.locator('#panelBody [data-tab="summary"]')


def activity(page: Page):
    return page.locator('#panelBody [data-tab="activity"]')


# ------------------------------------------------------------------ grouping, counting and trimming
def run(page: Page, body: str) -> Any:
    return page.evaluate(f"async () => {{ const s = await import('/js/summary.js'); {body} }}")


def test_narration_opens_a_group_and_its_tools_fold_into_counts(page: Page) -> None:
    groups = run(page, f"return s.groupActivity(s.traceRows({json.dumps(trace(1000))}))"
                       ".map(g => [g.narration, s.countsText(g.counts)]);")
    assert groups == [
        [None, "4 commands · 2 edits"],                                        # a step with only tools
        ["Reading the legacy drawer first. It has fourteen behaviours.", "3 edits · 2 commands · 1 read"],
        [SAID, ""],                                                            # nothing after it yet
    ]


def test_a_new_step_closes_the_open_narration(page: Page) -> None:
    events = [{"kind": "text", "summary": "Porting filters.", "ts": 1},
              tool("edit", "Edit", "a.ts", 2),
              {"kind": "step", "step": 1, "status": "running", "summary": "next", "ts": 3},
              tool("bash", "Bash", "make", 4), tool("think", "", "thinking…", 5),
              {"kind": "error", "summary": "boom", "ts": 6}]
    groups = run(page, f"return s.groupActivity(s.traceRows({json.dumps(events)})).map(g => [g.narration, s.countsText(g.counts)]);")
    assert groups == [["Porting filters.", "1 edit"], [None, "1 command · 1 error"]]


def test_one_line_is_the_first_sentence_cut_at_a_word(page: Page) -> None:
    lines = run(page, """return [
      s.oneLine('Draft is in outbox/par-by-weekday.md. Next the reader.'),
      s.oneLine('Upgraded to v5.2 and it works'),
      s.clip('one two three four five six seven', 20),
      s.oneLine('x'.repeat(10) + ' ' + 'word '.repeat(30)),
    ];""")
    assert lines[0] == "Draft is in outbox/par-by-weekday.md."
    assert lines[1] == "Upgraded to v5.2 and it works"
    assert lines[2] == "one two three four…"
    assert len(lines[3]) <= 80 and lines[3].endswith("word…")


def test_an_idle_session_shows_the_question_its_last_message_ended_on(page: Page) -> None:
    events = [{"kind": "text", "summary": "Step 4 gives legacy pages one way to start up.",
               "ask": "Should I send that sample to fleet?", "ts": 1}]
    wait = run(page, f"""const rows = s.traceRows({json.dumps(events)});
      const [now] = s.summarySections('k', {{status: 'idle'}}, {{}}, rows, [], new Set());
      return new DOMParser().parseFromString(now, 'text/html').querySelector('.sm-wait').textContent;""")
    assert "Should I send that sample to fleet?" in wait


def test_the_trace_grows_as_the_window_slides(page: Page) -> None:
    kept = run(page, """
      const ev = i => ({ kind: 'tool', tool: 'bash', summary: 'c' + i, ts: i });
      const w = (a, b) => Array.from({ length: b - a }, (_, i) => ev(a + i));
      s.noteTrace('t', w(0, 15));
      s.noteTrace('t', w(5, 20));          // overlaps: five new
      s.noteTrace('t', w(5, 20));          // the same window again: nothing new
      const grown = s.noteTrace('t', w(30, 32)).length;   // after a gap: appended
      const reset = s.noteTrace('t', w(0, 3)).length;     // older than what we hold: a different history
      return [grown, reset];""")
    assert kept == [22, 3]


# ------------------------------------------------------------------ the panel
def test_a_job_opens_on_its_summary(deck: Page, base_url: str, fixture_data: dict[str, Any]) -> None:
    deck.evaluate("doc => fleetDeck.apply(doc)", with_trace(state(base_url), RUNNING, trace(fixture_data["time"] - 700)))
    open_panel(deck, RUNNING)
    expect(deck.locator('#panelTabs [role="tab"]')).to_have_text(["Summary", "Activity", "History"])
    expect(deck.locator('#panelTabs [aria-selected="true"]')).to_have_text("Summary")
    expect(summary(deck)).to_be_visible()
    expect(activity(deck)).to_be_hidden()
    now = summary(deck).locator('[data-part="now"]')
    expect(now.locator(".sm-step")).to_have_text("Step 2 of 2Build the V2 route behind the feature flag")
    expect(now.locator(".sm-todo")).to_have_text("▸ Wire the supplier drawer")
    expect(now.locator(".sm-say")).to_contain_text(SAID)
    expect(now.locator(".sm-say time")).to_have_text("11m ago")
    expect(summary(deck).locator('[data-part="progress"] li')).to_have_text(
        ["✓ Port filters and sorting", "▸ Wire the supplier drawer", "· Add Playwright coverage"])
    recent = summary(deck).locator(".sm-recent li")
    expect(recent.locator(".sm-line")).to_have_text([
        "Every job now gets the writing guide in its preamble, so reports come out…",
        "Reading the legacy drawer first."])
    expect(recent.locator(".sm-tools")).to_have_text(["3 edits · 2 commands · 1 read", "Worked: 4 commands · 2 edits"])
    expect(summary(deck)).not_to_contain_text("thinking")
    expect(summary(deck).locator('[data-part="finished"]')).to_contain_text("1. Documented 14 behaviours in docs/suppliers-v2.md")
    expect(activity(deck)).to_contain_text("thinking…")                 # the raw trace keeps everything


def test_the_chosen_tab_is_remembered_per_browser(deck: Page) -> None:
    open_panel(deck, RUNNING)
    deck.locator('#panelTabs [data-tab="activity"]').click()
    expect(activity(deck)).to_be_visible()
    expect(summary(deck)).to_be_hidden()
    expect(activity(deck).locator("h3").first).to_have_text("Job")
    deck.locator("#panel #close").click()
    open_panel(deck, ASKING)                                           # another agent opens on the same tab
    expect(deck.locator('#panelTabs [aria-selected="true"]')).to_have_text("Activity")
    assert deck.evaluate("localStorage.getItem('fleet.panel.tab')") == "activity"
    deck.locator('#panelTabs [data-tab="summary"]').click()
    expect(summary(deck)).to_be_visible()


def test_a_job_with_documents_has_a_documents_tab(deck: Page) -> None:
    open_panel(deck, FAILED)
    expect(deck.locator('#panelTabs [role="tab"]')).to_have_text(["Summary", "Activity", "Documents", "History"])
    deck.locator('#panelTabs [data-tab="documents"]').click()
    expect(deck.locator('#panelBody [data-tab="documents"] .docs li')).to_have_count(1)
    deck.locator("#panel #close").click()
    open_panel(deck, RUNNING)                                          # no documents: the Summary stands in
    expect(summary(deck)).to_be_visible()
    assert deck.evaluate("localStorage.getItem('fleet.panel.tab')") == "documents"


def test_a_count_jumps_to_its_moment_in_activity(deck: Page, base_url: str, fixture_data: dict[str, Any]) -> None:
    deck.evaluate("doc => fleetDeck.apply(doc)", with_trace(state(base_url), RUNNING, trace(fixture_data["time"] - 700)))
    open_panel(deck, RUNNING)
    deck.locator(".sm-tools", has_text="Worked: 4 commands").click()
    expect(activity(deck)).to_be_visible()
    expect(deck.locator('#panelTabs [aria-selected="true"]')).to_have_text("Activity")
    marked = activity(deck).locator(".evs li.hl")
    expect(marked).to_have_count(6)
    expect(marked.first).to_contain_text("frontend/src/v2/part1.tsx")   # newest first
    expect(marked.last).to_contain_text("pnpm vitest run part0")
    expect(marked.last).to_be_in_viewport()
    assert deck.evaluate("localStorage.getItem('fleet.panel.tab')") is None   # a jump isn't a choice


def test_the_summary_updates_live_keeping_scroll_and_expanded_lines(deck: Page, base_url: str,
                                                                   fixture_data: dict[str, Any]) -> None:
    t = fixture_data["time"] - 700
    doc = state(base_url)
    deck.evaluate("doc => fleetDeck.apply(doc)", with_trace(doc, RUNNING, trace(t)))
    open_panel(deck, RUNNING)
    older = summary(deck).locator(".sm-line", has_text="Reading the legacy drawer")
    older.click()
    expect(older).to_have_attribute("aria-expanded", "true")
    expect(older).to_have_text("Reading the legacy drawer first. It has fourteen behaviours.")
    deck.evaluate("document.getElementById('panelBody').scrollTop = 60")
    scrolled = deck.evaluate("document.getElementById('panelBody').scrollTop")

    later = trace(t)[-12:] + [tool("edit", "Edit", "frontend/src/v2/reader.tsx", t + 30),
                              {"kind": "text", "summary": "The reader shows diagrams now.", "ts": t + 40},
                              tool("bash", "Bash", "pnpm test", t + 41)]                 # the window slid on
    deck.wait_for_timeout(250)                                        # past the scroll hold
    deck.evaluate("doc => fleetDeck.apply(doc)", with_trace(doc, RUNNING, later))
    expect(summary(deck).locator(".sm-line").first).to_have_text("The reader shows diagrams now.")
    expect(summary(deck).locator(".sm-tools").first).to_have_text("1 command")
    expect(summary(deck).locator(".sm-tools").nth(1)).to_have_text("1 edit")
    expect(summary(deck).locator(".sm-tools", has_text="Worked:")).to_have_text("Worked: 4 commands · 2 edits")   # kept from before the slide
    expect(older).to_have_attribute("aria-expanded", "true")
    assert deck.evaluate("document.getElementById('panelBody').scrollTop") == scrolled


def test_an_interactive_session_has_the_same_summary(deck: Page) -> None:
    open_panel(deck, ASKING)
    expect(deck.locator('#panelTabs [role="tab"]')).to_have_text(["Summary", "Activity", "History"])
    now = summary(deck).locator('[data-part="now"]')
    expect(now.locator(".sm-wait b")).to_have_text("Waiting for you")
    expect(now.locator(".sm-wait")).to_contain_text("Keep the double fetch behind a flag")
    expect(now.locator(".sm-step")).to_have_count(0)                   # sessions have no steps
    expect(now.locator(".sm-say")).to_have_text("No narration yet.")
    expect(summary(deck).locator(".sm-tools")).to_have_text(["Worked: 1 read · 1 search · 1 question"])
    expect(summary(deck).locator('[data-part="finished"]')).to_be_empty()
    now.locator("[data-answer]").click()
    expect(deck.locator("#reader")).to_be_visible()
    deck.keyboard.press("Escape")


def test_a_job_waiting_on_you_says_so_first(deck: Page, base_url: str) -> None:
    doc = state(base_url)
    for item in doc["attention"]:
        if item["kind"] == "decision":
            item["owner"] = {**item["owner"], "key": RUNNING, "type": "job", "host": "home", "id": "a1c3e9"}
    deck.evaluate("doc => fleetDeck.apply(doc)", doc)
    open_panel(deck, RUNNING)
    wait = summary(deck).locator('[data-part="now"] > :nth-child(2)')
    expect(wait).to_have_class("sm-wait")
    expect(wait.locator("[data-answer]")).to_be_visible()


def test_the_summary_keeps_to_its_text_budget(deck: Page, base_url: str, fixture_data: dict[str, Any]) -> None:
    t, long = fixture_data["time"] - 5000, " ".join(["word"] * 40)
    events = []
    for i in range(30):
        events += [{"kind": "text", "summary": f"{long}. More.", "ts": t + i * 10},
                   tool("edit", "Edit", "a.ts", t + i * 10 + 1), tool("bash", "Bash", "make", t + i * 10 + 2),
                   tool("read", "Read", "b.ts", t + i * 10 + 3), {"kind": "error", "summary": "x", "ts": t + i * 10 + 4}]
    doc = with_trace(state(base_url), RUNNING, events)
    for h in doc["hosts"]:
        for job in h["jobs"]:
            if f"{h['name']}:{job['id']}" == RUNNING:
                job["todos"] = [{"text": long, "status": "completed" if i < 6 else "in_progress" if i == 6 else "pending"} for i in range(14)]
                job["steps"] = [{"index": i, "title": long, "status": "done", "result": f"{long}\nsecond line"} for i in range(9)] + \
                    [{"index": 9, "title": long, "status": "running", "result": None}]
    for item in doc["attention"]:
        if item["kind"] == "decision":
            item["owner"] = {**item["owner"], "key": RUNNING}
            item["summary"] = long
    deck.evaluate("doc => fleetDeck.apply(doc)", doc)
    open_panel(deck, RUNNING)
    budget = run(deck, "return s.SUMMARY_BUDGET;")
    for part in ("now", "progress", "recently", "finished"):
        words = deck.evaluate(f"fleetDeck.textBudget(document.querySelector('#panelBody [data-tab=summary] [data-part={part}]'))")
        assert 0 < words <= budget[part], part
    total = deck.evaluate("fleetDeck.textBudget(document.querySelector('#panelBody [data-tab=summary]'))")
    assert total <= budget["total"]
    assert deck.evaluate("fleetDeck.textBudget(document.querySelector('#panelBody [data-tab=activity]'))") == 0   # hidden


def test_decisions_since_dispatch_in_job_panel(deck: Page, base_url: str, request) -> None:
    doc = state(base_url)
    for host in doc['hosts']:
        for job in host['jobs']:
            if f"{host['name']}:{job['id']}" == RUNNING:
                job['decisions_since_dispatch'] = [dict(id='decision-example', question='Which colour?',
                    answer='Use blue', actor='user', principle='Brief: clear choices')]
    deck.evaluate('doc => fleetDeck.apply(doc)', doc)
    open_panel(deck, RUNNING)
    deck.get_by_role('tab', name='Activity', exact=True).click()
    expect(activity(deck)).to_contain_text('Decisions since dispatch · 1')
    expect(activity(deck)).to_contain_text('Which colour?')
    expect(activity(deck)).to_contain_text('Use blue')
    expect(activity(deck)).to_contain_text('Brief: clear choices')
    shots = request.config.getoption('--shots')
    if shots:
        from pathlib import Path
        Path(shots).mkdir(parents=True, exist_ok=True)
        deck.screenshot(path=str(Path(shots) / 'decisions-since-dispatch.png'))
