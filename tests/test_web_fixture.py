"""The deck's HTTP API served from a recorded fleet (`fleet web --fixture`)."""

import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from conftest import FIXTURE
from fleet.cli import build_parser
from fleet.web.server import WEB_ROOT


def get(base_url: str, path: str, **query: str) -> dict:
    with urlopen(base_url + path + ("?" + urlencode(query) if query else ""), timeout=5) as response:
        return json.load(response)


def status_of(base_url: str, path: str, **query: str) -> int:
    with pytest.raises(HTTPError) as raised:
        urlopen(base_url + path + "?" + urlencode(query), timeout=5)
    return raised.value.code


def test_state_is_the_recorded_fleet(base_url: str, fixture_data: dict[str, Any]) -> None:
    state = get(base_url, "/api/state")
    assert state == {key: fixture_data[key] for key in ("time", "project_labels", "hosts")}
    hosts = {host["name"]: host for host in state["hosts"]}
    assert hosts["gpu-box"]["ok"] is False and hosts["gpu-box"]["error"]
    restoke = [job for host in state["hosts"] for job in host["jobs"] if job["project"] == "restoke"]
    assert sorted(job["status"] for job in restoke) == ["done", "failed", "running", "running", "running"]
    assert {session["agent"] for host in state["hosts"] for session in host["sessions"]} == {"claude", "codex"}


def test_stream_pushes_the_recorded_state(base_url: str) -> None:
    with urlopen(base_url + "/api/stream", timeout=5) as response:
        assert response.readline() == b"event: state\n"
        payload = json.loads(response.readline().decode().removeprefix("data: "))
    assert [host["name"] for host in payload["hosts"]] == ["home", "worker", "gpu-box"]


def test_job_documents_render_like_a_live_host(base_url: str) -> None:
    document = get(base_url, "/api/doc", host="home", job="e1b5c8", id="report-0")
    assert document["job_description"] == "Upgrade Django to 5.2"
    assert document["name"] == "Step 1: Bump Django and run the test suite"
    assert "FLEET_STATUS" not in document["markdown"]
    assert [entry["text"] for entry in document["toc"]] == ["Django 5.2 upgrade blocked", "Next"]
    outbox = get(base_url, "/api/doc", host="worker", job="d4f7a2", id="outbox-par-by-weekday.md")
    assert "<table>" in outbox["html"]


def test_unknown_job_documents_are_refused(base_url: str) -> None:
    assert status_of(base_url, "/api/doc", host="gpu-box", job="e1b5c8", id="report-0") == 404
    assert status_of(base_url, "/api/doc", host="worker", job="c90e11", id="report-0") == 404
    assert status_of(base_url, "/api/doc", host="nowhere", job="e1b5c8", id="report-0") == 400


def test_library_lists_and_reads_fixture_markdown(base_url: str) -> None:
    documents = get(base_url, "/api/library")["documents"]
    assert [(doc["project"], doc["id"], doc["title"]) for doc in documents] == [
        ("agent-fleet", "README.md", "agent-fleet"),
        ("restoke", "README.md", "Restoke"),
        ("restoke", "docs/suppliers-v2.md", "Suppliers V2")]
    document = get(base_url, "/api/library/doc", project="restoke", id="docs/suppliers-v2.md")
    assert 'type="checkbox"' in document["html"]
    assert status_of(base_url, "/api/library/doc", project="restoke", id="docs/missing.md") == 404


@pytest.mark.parametrize("directory, content_type", [("css", "text/css"), ("js", "text/javascript")])
def test_deck_code_is_served_fresh_with_its_type(base_url: str, directory: str, content_type: str) -> None:
    files = sorted((WEB_ROOT / directory).rglob("*.*"))
    assert files
    for path in files:
        with urlopen(f"{base_url}/{path.relative_to(WEB_ROOT).as_posix()}", timeout=5) as response:
            assert response.headers["Content-Type"].startswith(content_type)
            assert response.headers["Cache-Control"] == "no-store"
            assert response.read() == path.read_bytes()
    assert status_of(base_url, f"/{directory}/../server.py") == 404


def test_fixture_is_a_hidden_web_option() -> None:
    assert build_parser().parse_args(["web", "--fixture", str(FIXTURE)]).fixture == str(FIXTURE)
    assert "--fixture" not in build_parser()._subparsers._group_actions[0].choices["web"].format_help()
