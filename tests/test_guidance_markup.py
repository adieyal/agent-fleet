"""The room's guidance markup, rendered by guidance.js under Node with only its two imports stubbed.

The browser tests in test_deck_browser.py drive the same markup through the deck; this checks it where no browser runs.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "packages" / "fleet-web" / "src" / "fleet_web" / "static" / "js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

VERSION = dict(revision="abc", number=2, actor="web-user", time="2026-10-01T09:00:00+02:00", source_run=None)
VIEW = dict(name="Charter: T · version 2", html="<h2>Goal</h2><p>Read &amp; store.</p>",
            markdown="## Goal\n\nRead & store.\n",
            guidance=dict(path="charters/e.md", version=VERSION, inherits=dict(VERSION, number=1),
                          constitution=dict(VERSION, number=3)),
            history=[VERSION, dict(VERSION, number=1, actor="user")])
DECISIONS = dict(charter=True, decisions=[
    dict(id="d1d1d1d1-0000", question="Loosen <the> check?", answer="No", actor="codex", principle=None,
         time="2026-10-01T09:00:00+00:00", work_items=[dict(id="t1", title="Task")], promoted=False),
    dict(id="d2d2d2d2-0000", question="Q2", answer="A2", actor="claude", principle="Constitution: anti-goal 2",
         time="2026-09-30T09:00:00+00:00", work_items=[dict(id="t2", title=None)], promoted=True)])


def render(tmp_path: Path, script: str) -> dict:
    (tmp_path / "package.json").write_text('{"type":"module"}\n')
    (tmp_path / "guidance.js").write_text((JS / "guidance.js").read_text())
    escape = re.search(r"^export function esc\(.*$", (JS / "util.js").read_text(), re.MULTILINE)[0]
    (tmp_path / "util.js").write_text(escape + "\n")
    (tmp_path / "panel.js").write_text("export const idChip = id => `<button data-copy-id=\"${id}\">${id.slice(0, 8)}</button>`;\n")
    (tmp_path / "run.mjs").write_text("import * as g from './guidance.js';\n"
                                      f"const VIEW = {json.dumps(VIEW)}, DECISIONS = {json.dumps(DECISIONS)};\n"
                                      f"console.log(JSON.stringify({script}));\n")
    result = subprocess.run(["node", str(tmp_path / "run.mjs")], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_charter_panel_shows_version_inheritance_and_icon_tools(tmp_path):
    html = render(tmp_path, "g.guidancePanel('charter', VIEW)")
    assert '<h3>Charter</h3>' in html and "version 2 · web-user" in html
    assert "Inherits constitution version 1; current version 3" in html
    assert "<p>Read &amp; store.</p>" in html
    assert 'data-guidance-edit="charter" title="Edit the charter"' in html and 'data-guidance-history="charter"' in html
    assert "data-guidance-versions" not in html and "<textarea" not in html


def test_history_lists_versions_that_open_in_the_reader(tmp_path):
    html = render(tmp_path, "g.guidancePanel('constitution', VIEW, { history: true })")
    assert html.count("data-guidance-open=\"constitution\"") == 2 and 'data-version="1"' in html
    assert "Inherits" not in html


def test_editor_holds_escaped_text_and_its_error(tmp_path):
    html = render(tmp_path, "g.guidancePanel('charter', VIEW, { editing: { text: '<b>x</b> & y', base: 2, "
                            "error: 'changed since version 2: it is now version 3' } })")
    assert "<textarea data-guidance-text" in html and "&lt;b&gt;x&lt;/b&gt; &amp; y</textarea>" in html
    assert "Editing version 2" in html and 'role="alert">changed since version 2' in html
    assert "data-guidance-edit=" not in html and "data-guidance-body" not in html


@pytest.mark.parametrize("view, expected", [
    (dict(guidance=None, history=[]), ["No charter recorded.", 'title="Write the charter"']),
    (dict(error="boom"), ['role="alert">boom']),
])
def test_empty_states_are_named(tmp_path, view, expected):
    html = render(tmp_path, f"g.guidancePanel('charter', {json.dumps(view)})")
    for text in expected:
        assert text in html
    assert "data-guidance-history" not in html


def test_decisions_show_principle_promote_and_in_force(tmp_path):
    html = render(tmp_path, "g.decisionsPanel(DECISIONS)")
    first, second = html.split("<li data-decision=")[1:]
    assert "Loosen &lt;the&gt; check?" in first and "Principle unknown" in first
    assert 'data-promote="d1d1d1d1-0000"' in first and "data-in-force" not in first
    assert "Principle: Constitution: anti-goal 2" in second and "In force" in second and "data-promote" not in second
    assert "codex · " in first and "Task" in first and "t2" in second
    empty = render(tmp_path, "g.decisionsPanel({ charter: false, decisions: [] })")
    assert "No decisions recorded." in empty


def test_floor_summary(tmp_path):
    summaries = render(tmp_path, "[g.guidanceSummary(VIEW), g.guidanceSummary({ guidance: null }), "
                                 "g.guidanceSummary(undefined)]")
    assert summaries[0].startswith("version 2 · web-user")
    assert summaries[1:] == ["Not recorded", "Loading…"]
