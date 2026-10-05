"""Action glyphs (packages/fleet-web/src/fleet_web/static/js/glyphs.js): the one mapping from an agent's state and latest tool event to the small
picture its bubble shows, run in the browser against the module the deck serves."""

from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser, Page
from fleet.projections.activity import event_activity

IMPORT = "import('/js/glyphs.js')"


@pytest.fixture(scope="module")
def module_page(browser: Browser, base_url: str) -> Iterator[Page]:
    """A plain page on the deck's origin, so the module and its imports load without the 3D deck."""
    page = browser.new_page()
    page.goto(base_url + "/api/state")
    yield page
    page.close()


def action_of_event(page: Page, event: dict[str, Any] | None) -> str:
    return page.evaluate(f"event => {IMPORT}.then(glyphs => glyphs.actionOfEvent(event))", event_activity(event))


def tool(name: str, summary: str = "", **extra: Any) -> dict[str, Any]:
    return {"kind": "tool", "name": name, "summary": summary, **extra}


@pytest.mark.parametrize(("event", "action"), [
    (tool("Read", "packages/fleet-web/src/fleet_web/static/js/agents.js"), "read"),
    (tool("Grep", "actionOf"), "search"),
    (tool("Glob", "**/*.py"), "search"),
    (tool("WebFetch", "https://example.com"), "web"),
    (tool("Edit", "fleet/projects.py"), "edit"),
    (tool("Write", "docs/notes.md"), "doc"),
    (tool("MultiEdit", "packages/fleet-cli/src/fleet_cli/cli.py"), "edit"),
    (tool("apply_patch", "a.py, b.py"), "edit"),
    (tool("Bash", "cd /src && uv run pytest -q tests"), "test"),
    (tool("Bash", "npm test"), "test"),
    (tool("Bash", "pnpm vitest run suppliers"), "test"),
    (tool("shell", "bash -lc 'pytest -q'"), "test"),
    (tool("Bash", "git status"), "review"),
    (tool("Bash", "ls -la"), "search"),                 # looking around, as the deck already classes it
    (tool("BashOutput"), "wait"),
    ({"kind": "tool", "tool": "bash", "summary": "make build"}, "build"),
    ({"kind": "text", "summary": "Let me think about this"}, "think"),
    (tool("TodoWrite"), "plan"),
    (None, "think"),
    (tool("AskUserQuestion", "Ship it?"), "wait"),
    ({"kind": "error", "summary": "boom"}, "error"),
    (tool("Task", "explore the repo"), "delegate"),
    (tool("mcp__docs__search_everything"), "type"),
])
def test_tool_events_map_to_actions(module_page: Page, event: dict[str, Any] | None, action: str) -> None:
    assert action_of_event(module_page, event) == action


@pytest.mark.parametrize(("item", "action"), [
    ({"status": "idle", "activity": tool("Edit", "a.py")}, "wait"),          # a session waiting on its human
    ({"status": "working", "activity": tool("Edit", "a.py")}, "edit"),
    ({"status": "running", "activity": tool("Bash", "pytest")}, "test"),
    ({"status": "running", "activity": None}, "think"),
    ({"status": "queued", "activity": None}, "queued"),
    ({"status": "done", "activity": tool("Edit", "a.py")}, "done"),
    ({"status": "failed", "activity": None}, "failed"),
    ({"status": "stalled", "activity": None}, "stalled"),
    ({"status": "cancelled", "activity": None}, "cancelled"),
])
def test_status_comes_before_the_latest_event(module_page: Page, item: dict[str, Any], action: str) -> None:
    projected = {**item, "activity": event_activity(item["activity"])}
    assert module_page.evaluate(f"item => {IMPORT}.then(glyphs => glyphs.actionOf(item))", projected) == action


def test_every_action_has_a_labelled_glyph(module_page: Page) -> None:
    glyphs = module_page.evaluate(f"""{IMPORT}.then(glyphs => Object.keys(glyphs.ACTIONS).map(action => {{
        const box = document.createElement('div');
        box.innerHTML = glyphs.glyphHtml(action);
        const glyph = box.firstChild;
        return [action, glyph.dataset.action, glyph.getAttribute('role'), glyph.getAttribute('aria-label'),
                glyph.querySelector('svg path, svg circle') !== null, glyph.textContent.trim()];
    }}))""")
    assert {row[0] for row in glyphs} >= {"search", "read", "edit", "test", "type", "think", "wait"}
    assert not {row[0] for row in glyphs} & {"ask", "shell"}
    for action, data_action, role, label, drawn, text in glyphs:
        assert data_action == action and role == "img" and label and drawn and text == ""


def test_server_activity_classes_have_glyphs(module_page: Page) -> None:
    classes = ['read', 'search', 'edit', 'test', 'wait', 'web', 'plan', 'delegate',
               'type', 'doc', 'ship', 'review', 'build', 'think', 'unknown']
    assert module_page.evaluate(f"""classes => {IMPORT}.then(glyphs => classes.every(action => {{
        const box = document.createElement('div');
        box.innerHTML = glyphs.glyphHtml(action);
        return box.firstChild.dataset.action === action && box.querySelector('svg') !== null;
    }}))""", classes)
