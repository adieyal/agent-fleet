"""Explicit state paths must not silently create a new workspace."""

from pathlib import Path

import pytest
from rich.console import Console

from fleet import cli


@pytest.mark.parametrize("arguments", [["project", "ls"], ["host", "add", "demo", "--local"], ["web"]])
def test_missing_config_fails_before_creating_store(tmp_path, monkeypatch, arguments, override_cli_method, cli_container):
    root = tmp_path / "deleted-preview"
    config = root / "config.json"
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.setenv("FLEET_STORE", str(root / "fleet.db"))
    errors = Console(record=True, width=300, no_color=True)
    monkeypatch.setattr(cli, "error_console", errors)
    monkeypatch.setattr(cli, "serve", lambda *args, **kwargs: None)
    override_cli_method('jobs', 'selected_hosts', lambda arguments: [])

    with pytest.raises(SystemExit) as exited:
        cli.main(arguments, container=cli_container)

    assert exited.value.code == 2
    assert "FLEET_CONFIG" in errors.export_text(clear=False)
    assert str(config) in errors.export_text()
    assert not root.exists()


@pytest.mark.parametrize("arguments", [["project", "ls"], ["host", "add", "demo", "--local"]])
def test_missing_store_fails_without_creating_file(tmp_path, monkeypatch, arguments):
    config = tmp_path / "config.json"
    config.write_text('{"hosts": {}}')
    store = tmp_path / "deleted-preview" / "fleet.db"
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.setenv("FLEET_STORE", str(store))
    errors = Console(record=True, width=300, no_color=True)
    monkeypatch.setattr(cli, "error_console", errors)

    with pytest.raises(SystemExit) as exited:
        cli.main(arguments)

    assert exited.value.code == 2
    assert "FLEET_STORE" in errors.export_text(clear=False)
    assert str(store) in errors.export_text()
    assert not store.parent.exists()
    assert config.read_text() == '{"hosts": {}}'


def test_web_announces_explicit_store_initialization(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text('{"hosts": {"demo": {"ssh": null}}}')
    store = tmp_path / "new-preview" / "fleet.db"
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.setenv("FLEET_STORE", str(store))
    errors = Console(record=True, width=300, no_color=True)
    monkeypatch.setattr(cli, "error_console", errors)
    monkeypatch.setattr(cli, "serve", lambda *args, **kwargs: None)

    cli.main(["web", "--port", "0"])

    assert store.is_file()
    assert "Creating new store" in errors.export_text(clear=False)
    assert str(store) in errors.export_text()
    cli.main(["web", "--port", "0"])
    assert errors.export_text() == ""


def test_default_first_use_creates_store_beside_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FLEET_CONFIG")
    monkeypatch.delenv("FLEET_STORE")

    cli.main(["project", "ls", "--no-suggest"])

    assert (Path.home() / ".config" / "fleet" / "fleet.db").is_file()
