"""Project registry: identity, explicit links, repository suggestions and config storage."""
import json

import pytest

from fleet import projects, transport
from fleet.projects import Link, Registry, Suggestion, normalize_repository
from fleet.transport import FleetError


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(transport, "CONFIG_PATH", path)
    return path


def test_new_projects_get_stable_distinct_ids():
    registry = Registry()
    first, second = registry.create("Agent Fleet"), registry.create("Agent Fleet")
    assert projects.PROJECT_ID.match(first.id) and projects.PROJECT_ID.match(second.id)
    assert first.id != second.id
    registry.rename(first.id, "Fleet")
    assert registry.get(first.id).name == "Fleet"


def test_matching_labels_on_other_hosts_stay_unregistered():
    registry = Registry()
    project = registry.create("Agent Fleet")
    registry.link(project.id, "home", "agent-fleet")
    assert registry.project_for("home", "agent-fleet") is project
    assert registry.project_for("work", "agent-fleet") is None
    assert registry.project_for("home", "Agent Fleet") is None


def test_one_project_spans_hosts_with_different_labels():
    registry = Registry()
    project = registry.create("Restoke supplier")
    registry.link(project.id, "home", "restoke")
    registry.link(project.id, "gpu", "supplier-slice")
    assert registry.project_for("gpu", "supplier-slice") is project
    assert registry.project_for("home", "restoke") is project


def test_a_pair_links_to_one_project_until_unlinked():
    registry = Registry()
    first, second = registry.create("One"), registry.create("Two")
    registry.link(first.id, "home", "shared")
    with pytest.raises(FleetError, match="already linked"):
        registry.link(second.id, "home", "shared")
    assert registry.unlink("home", "shared") == first.id
    registry.link(second.id, "home", "shared")
    assert registry.project_for("home", "shared") is second
    with pytest.raises(FleetError):
        registry.unlink("home", "unknown")


def test_removing_a_project_releases_its_links():
    registry = Registry()
    project = registry.create("Gone")
    registry.link(project.id, "home", "gone")
    registry.remove(project.id)
    assert registry.project_for("home", "gone") is None
    with pytest.raises(FleetError, match="unknown project"):
        registry.get(project.id)


def test_display_name_prefers_linked_project_then_project_labels():
    registry = Registry()
    project = registry.create("Agent Fleet")
    registry.link(project.id, "home", "agent-fleet")
    labels = {"agent-fleet": "Old sign", "restoke-analytics": "Bang bang!"}
    assert registry.display_name("home", "agent-fleet", labels) == "Agent Fleet"
    assert registry.display_name("work", "agent-fleet", labels) == "Old sign"
    assert registry.display_name("home", "restoke-analytics", labels) == "Bang bang!"
    assert registry.display_name("home", "other", labels) is None


@pytest.mark.parametrize("url", [
    "git@github.com:adieyal/agent-fleet.git",
    "https://github.com/adieyal/agent-fleet",
    "https://user@GitHub.com/adieyal/agent-fleet.git/",
    "ssh://git@github.com:22/adieyal/agent-fleet.git",
])
def test_repository_forms_normalize_equal(url):
    assert normalize_repository(url) == "github.com/adieyal/agent-fleet"


def test_repositories_only_suggest_links_for_unlinked_pairs():
    registry = Registry()
    fleet = registry.create("Agent Fleet", ["git@github.com:adieyal/agent-fleet.git"])
    registry.create("agent-fleet")  # same name, no repository: never suggested
    registry.link(fleet.id, "home", "agent-fleet")
    observed = [("home", "agent-fleet", "https://github.com/adieyal/agent-fleet"),
                ("gpu", "fleet-clone", "https://github.com/adieyal/agent-fleet.git"),
                ("gpu", "agent-fleet", "git@github.com:someone/else.git"),
                ("gpu", "scratch", "")]
    assert registry.suggest_links(observed) == [
        Suggestion(Link("gpu", "fleet-clone"), fleet.id, "https://github.com/adieyal/agent-fleet.git")]
    assert registry.project_for("gpu", "fleet-clone") is None


def test_duplicate_repository_forms_are_stored_once():
    registry = Registry()
    project = registry.create("Fleet", ["git@github.com:a/b.git", "https://github.com/a/b"])
    assert project.repositories == ["git@github.com:a/b.git"]
    registry.remove_repository(project.id, "https://github.com/a/b.git")
    assert project.repositories == []


def test_registry_round_trips_through_config_keeping_other_keys(config_path):
    config_path.write_text(json.dumps({"hosts": {"home": {}}, "project_labels": {"x": "X"}}))
    registry = projects.load_registry()
    project = registry.create("Agent Fleet", ["git@github.com:adieyal/agent-fleet.git"])
    registry.link(project.id, "home", "agent-fleet")
    projects.save_registry(registry)

    stored = json.loads(config_path.read_text())
    assert stored["hosts"] == {"home": {}} and stored["project_labels"] == {"x": "X"}
    assert stored["projects"][project.id]["links"] == [{"host": "home", "label": "agent-fleet"}]
    reloaded = projects.load_registry()
    assert reloaded.project_for("home", "agent-fleet").id == project.id


def test_config_without_projects_loads_empty(config_path):
    config_path.write_text(json.dumps({"hosts": {}, "project_labels": {"x": "X"}}))
    assert projects.load_registry().projects == {}


@pytest.mark.parametrize("entries, message", [
    ({"agent-fleet": {"name": "Bad id"}}, "invalid project id"),
    ({"p-00000001": {"name": "A", "links": [{"host": "h", "label": "l"}]},
      "p-00000002": {"name": "B", "links": [{"host": "h", "label": "l"}]}}, "already linked"),
])
def test_inconsistent_config_is_rejected(config_path, entries, message):
    config_path.write_text(json.dumps({"projects": entries}))
    with pytest.raises(FleetError, match=message):
        projects.load_registry()
