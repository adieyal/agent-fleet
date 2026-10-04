import os
from pathlib import Path

import pytest

from fleet import cli, transport
from fleet.composition import open_attention, store_path


def test_fleet_environment_is_private_to_each_test(tmp_path):
    for name in ("FLEET_CONFIG", "FLEET_STORE", "FLEET_HOME"):
        assert Path(os.environ[name]).is_relative_to(tmp_path)


def test_config_and_store_paths_follow_environment(monkeypatch, tmp_path):
    config = tmp_path / "alternate" / "config.json"
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.delenv("FLEET_STORE")
    assert store_path() == config.parent / "fleet.db"
    transport.save_config({"hosts": {}})
    assert transport.load_config() == {"hosts": {}}
    assert config.exists()


def test_default_config_path_follows_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FLEET_CONFIG")
    monkeypatch.delenv("FLEET_STORE")
    assert store_path() == tmp_path / ".config" / "fleet" / "fleet.db"


def snapshot(directory):
    return {path.relative_to(directory): (path.stat().st_mtime_ns, path.read_bytes())
            for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("command", ["attention", "status"])
def test_attention_import_and_status_leave_real_config_untouched(monkeypatch, tmp_path, capsys, command, *, cli_container):
    # A populated stand-in for the original HOME, never the user's live files.
    original_home = tmp_path / "original-home"
    original_config = original_home / ".config" / "fleet"
    original_config.mkdir(parents=True)
    (original_config / "config.json").write_text('{"hosts": {"sentinel": {}}}')
    (original_config / "fleet.db").write_bytes(b"original-store-sentinel")
    monkeypatch.setenv("HOME", str(original_home))
    before = snapshot(original_config)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    config = tmp_path / "config" / "config.json"
    config.parent.mkdir(exist_ok=True)
    workspace = config.with_name("workspace.json")
    workspace.write_text('{"attention": {}}')
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.setenv("FLEET_STORE", str(config.with_name("fleet.db")))
    if command == "attention":
        open_attention()
    else:
        identity = cli_container.initialized_workspace().edit_registry(lambda registry: registry.create('p')).id
        cli.main(["status", "p", "--json"], container=cli_container)
        assert f'"project": "{identity}"' in capsys.readouterr().out
    assert snapshot(original_config) == before
    assert not home.exists()
    assert workspace.with_suffix(".json.bak").read_bytes() == workspace.read_bytes()
    assert config.with_name("fleet.db").exists()
