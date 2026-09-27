"""`fleet project …` end to end: the argument parser, the config file and git remotes on a local host."""
import json
import subprocess

import pytest
from rich.console import Console

from fleet import cli, transport
from fleet.composition import open_workspace
from fleet.transport import HostReport


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hosts": {"home": {}, "gpu": {}}, "project_labels": {"old": "Old sign"}}))
    monkeypatch.setattr(transport, "CONFIG_PATH", path)
    return path


@pytest.fixture
def fleet(monkeypatch):
    """Run the CLI and return what it printed."""
    def run(*arguments: str) -> str:
        output = Console(width=200, no_color=True, record=True)
        monkeypatch.setattr(cli, "console", output)
        cli.main(list(arguments))
        return output.export_text()
    return run


def stored_projects(config_path):
    return open_workspace().registry().to_config()


def only_id(config_path):
    (project_id,) = stored_projects(config_path)
    return project_id


def test_add_with_links_keeps_other_config(config_path, fleet):
    output = fleet("project", "add", "Agent Fleet", "--link", "home:agent-fleet", "--link", "gpu:fleet")
    project_id = only_id(config_path)
    assert project_id in output
    stored = json.loads(config_path.read_text())
    assert stored["project_labels"] == {"old": "Old sign"} and set(stored["hosts"]) == {"home", "gpu"}
    assert "projects" not in stored
    stored["projects"] = stored_projects(config_path)
    assert isinstance(stored["projects"][project_id].pop("created_at"), float)
    assert stored["projects"][project_id] == {
        "name": "Agent Fleet", "repositories": [],
        "links": [{"host": "gpu", "label": "fleet"}, {"host": "home", "label": "agent-fleet"}]}


def test_rename_keeps_the_id(config_path, fleet):
    fleet("project", "add", "Agent Fleet", "--link", "home:agent-fleet")
    project_id = only_id(config_path)
    created = stored_projects(config_path)[project_id]["created_at"]
    fleet("project", "rename", project_id, "Fleet")
    assert stored_projects(config_path) == {project_id: {
        "name": "Fleet", "links": [{"host": "home", "label": "agent-fleet"}], "repositories": [], "created_at": created}}


def test_link_and_unlink(config_path, fleet):
    fleet("project", "add", "Agent Fleet")
    project_id = only_id(config_path)
    fleet("project", "link", project_id, "gpu:agent-fleet")
    assert "gpu:agent-fleet" in fleet("project", "ls", "--no-suggest")
    fleet("project", "unlink", "gpu:agent-fleet")
    assert stored_projects(config_path)[project_id]["links"] == []


@pytest.mark.parametrize("arguments", [
    ("project", "add", "X", "--link", "nowhere:x"),
    ("project", "add", "X", "--link", "home"),
    ("project", "link", "p-00000000", "home:x"),
    ("project", "unlink", "home:never-linked"),
    ("project", "rename", "p-00000000", "X"),
])
def test_bad_references_fail_without_writing(config_path, fleet, arguments):
    before = config_path.read_text()
    with pytest.raises(SystemExit) as exited:
        fleet(*arguments)
    assert exited.value.code == 2
    assert config_path.read_text() == before


def test_a_linked_label_cannot_be_taken_by_another_project(config_path, fleet):
    fleet("project", "add", "One", "--link", "home:shared")
    with pytest.raises(SystemExit):
        fleet("project", "add", "Two", "--link", "home:shared")
    assert [entry["name"] for entry in stored_projects(config_path).values()] == ["One"]


def test_merge_keeps_the_older_id_and_takes_the_others_links_and_repositories(config_path, fleet):
    fleet("project", "add", "Agent Fleet", "--link", "home:agent-fleet", "--repo", "git@github.com:adieyal/agent-fleet.git")
    (older,) = stored_projects(config_path)
    fleet("project", "add", "agent-fleet", "--link", "gpu:agent-fleet", "--link", "gpu:fleet-docs",
          "--repo", "https://github.com/adieyal/agent-fleet", "--repo", "git@github.com:adieyal/fleet-docs.git")
    (newer,) = set(stored_projects(config_path)) - {older}

    with pytest.raises(SystemExit):   # the newer one can't be kept
        fleet("project", "merge", newer, older)
    assert set(stored_projects(config_path)) == {older, newer}

    output = fleet("project", "merge", older, newer)
    assert f"merged {newer} agent-fleet into {older} Agent Fleet" in output
    stored = stored_projects(config_path)
    assert list(stored) == [older] and stored[older]["name"] == "Agent Fleet"
    assert stored[older]["links"] == [{"host": "gpu", "label": "agent-fleet"}, {"host": "gpu", "label": "fleet-docs"},
                                      {"host": "home", "label": "agent-fleet"}]
    assert stored[older]["repositories"] == ["git@github.com:adieyal/agent-fleet.git", "git@github.com:adieyal/fleet-docs.git"]


def test_merging_projects_of_unknown_age_keeps_the_one_named_first(config_path, fleet):
    config = json.loads(config_path.read_text())
    config["projects"] = {"p-0000000a": {"name": "A", "links": [{"host": "home", "label": "a"}], "repositories": []},
                          "p-0000000b": {"name": "B", "links": [{"host": "gpu", "label": "a"}], "repositories": []}}
    config_path.write_text(json.dumps(config))
    fleet("project", "merge", "p-0000000b", "p-0000000a")
    assert stored_projects(config_path) == {"p-0000000b": {
        "name": "B", "links": [{"host": "gpu", "label": "a"}, {"host": "home", "label": "a"}], "repositories": []}}


@pytest.mark.parametrize("arguments", [("p-0000000a", "p-0000000a"), ("p-0000000a", "p-00000000")])
def test_merge_refuses_itself_and_unknown_projects(config_path, fleet, arguments):
    config = json.loads(config_path.read_text())
    config["projects"] = {"p-0000000a": {"name": "A", "links": [], "repositories": []}}
    config_path.write_text(json.dumps(config))
    before = config_path.read_text()
    with pytest.raises(SystemExit):
        fleet("project", "merge", *arguments)
    assert config_path.read_text() == before


def git_repository(path, remote):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
    return str(path)


def test_ls_suggests_links_only_where_repositories_match(config_path, fleet, tmp_path, monkeypatch):
    matching = git_repository(tmp_path / "clone", "https://github.com/adieyal/agent-fleet.git")
    other = git_repository(tmp_path / "other", "git@github.com:someone/agent-fleet.git")
    linked = git_repository(tmp_path / "linked", "git@github.com:adieyal/agent-fleet.git")
    jobs = {"home": [{"project": "fleet-clone", "cwd": matching}, {"project": "agent-fleet", "cwd": linked}],
            "gpu": [{"project": "agent-fleet", "cwd": other}]}
    monkeypatch.setattr(transport, "gather",
                        lambda hosts, arguments: [HostReport(host, jobs[host.name]) for host in hosts])
    monkeypatch.setattr(transport, "gather_sessions", lambda hosts: {"home": [{"project": None, "cwd": None}]})

    fleet("project", "add", "Agent Fleet", "--repo", "git@github.com:adieyal/agent-fleet.git",
          "--link", "home:agent-fleet")
    project_id = only_id(config_path)
    output = fleet("project", "ls")

    assert f"home:fleet-clone → {project_id} Agent Fleet" in output
    assert f"fleet project link {project_id} home:fleet-clone" in output
    assert "gpu:agent-fleet" not in output
    assert "home:agent-fleet →" not in output


def test_ls_reports_hosts_it_could_not_ask(config_path, fleet, monkeypatch):
    monkeypatch.setattr(transport, "gather", lambda hosts, arguments: [HostReport(host, [], "down") for host in hosts])
    monkeypatch.setattr(transport, "gather_sessions", lambda hosts: {})
    fleet("project", "add", "Agent Fleet", "--repo", "git@github.com:adieyal/agent-fleet.git")
    output = fleet("project", "ls")
    assert "no suggestions from down" in output
    assert "suggested links" not in output
