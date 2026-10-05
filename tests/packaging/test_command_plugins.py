"""The web command is a distribution plugin, with explicit missing/duplicate failures."""
from types import SimpleNamespace

import pytest

from fleet.api import FleetError
from fleet_cli import plugins
from fleet_cli.cli import command_web


@pytest.mark.parametrize('count', [0, 2])
def test_web_registration_must_be_unique(monkeypatch, count):
    entries = [SimpleNamespace(name='web', load=lambda: pytest.fail('ambiguous plugin loaded')) for _ in range(count)]
    monkeypatch.setattr(plugins, 'entry_points', lambda **_: entries)
    with pytest.raises(FleetError, match=f'found {count}'):
        plugins.load_command('web')


def test_cli_passes_the_same_arguments_and_container_to_plugin(monkeypatch):
    calls = []
    handler = lambda arguments, container: calls.append((arguments, container))
    monkeypatch.setattr(plugins, 'entry_points', lambda **_: [SimpleNamespace(name='web', load=lambda: handler)])
    arguments, container = object(), object()
    command_web(arguments, container=container)
    assert calls == [(arguments, container)]
