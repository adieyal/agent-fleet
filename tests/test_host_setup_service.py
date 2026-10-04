"""Host setup is usable without a CLI and preserves worker and agent configuration."""
import json
from types import SimpleNamespace

import pytest

from fleet import transport
from fleet.services.hosts import HostSetup, merge_detected


def test_agent_probe_uses_first_hit_and_keeps_node_directories_on_path():
    found = merge_detected('PATH=/bin:/usr/bin\nclaude=/opt/nvm/bin/claude\ncodex=\n'
                           'PATH=/other/bin\nclaude=/later/claude\ncodex=/opt/codex/bin/codex\n')
    assert found == {'path': '/opt/nvm/bin:/opt/codex/bin:/bin:/usr/bin',
                     'claude': '/opt/nvm/bin/claude', 'codex': '/opt/codex/bin/codex'}


@pytest.mark.parametrize('local', [False, True])
def test_install_copies_standalone_worker_and_configures_detected_agents(monkeypatch, local):
    monkeypatch.setenv('SSH_AUTH_SOCK', '/controller/agent.sock')
    host = transport.Host('worker', None if local else 'worker.example')
    copies, commands, configurations = [], [], []
    responses = iter(['', 'PATH=/bin\nclaude=/opt/node/bin/claude\ncodex=\n', ''])

    def shell(host, command, **options):
        commands.append((command, options))
        return SimpleNamespace(stdout=next(responses))

    def call(host, arguments):
        configurations.append(json.loads(arguments[1]))
        return {'host': 'worker', 'config': configurations[-1], 'tmux': True}

    adapter = SimpleNamespace(host_by_name=lambda name: host, run_shell=shell,
        LOCAL_FLEETD_SOURCE=transport.LOCAL_FLEETD_SOURCE, REMOTE_FLEETD_PATH=transport.REMOTE_FLEETD_PATH,
        rsync=lambda sources, destination, target: copies.append((sources, destination, target)), call=call)
    installed, report, socket = HostSetup(adapter).install('worker')
    assert installed == host
    assert copies[0][0] == [str(transport.LOCAL_FLEETD_SOURCE)]
    assert transport.LOCAL_FLEETD_SOURCE.name == 'fleetd.py'
    assert report['config'] == {'path': '/opt/node/bin:/bin', 'claude': '/opt/node/bin/claude',
                                'codex': None, 'ssh_auth_sock': '/controller/agent.sock' if local else None}
    assert socket == ('/controller/agent.sock' if local else '')
    assert [options.get('timeout') for _, options in commands] == [None, 60, 20]
    assert commands[0][1] == {'check': True, 'capture_output': True}


def test_unlock_uses_interactive_transport_and_quotes_key_path():
    calls = []
    host = transport.Host('worker', 'worker.example')
    adapter = SimpleNamespace(host_by_name=lambda name: host,
        run_shell=lambda target, command, **options: (calls.append((target, command, options)) or SimpleNamespace(returncode=7)))
    assert HostSetup(adapter).unlock('worker', '/keys/key with spaces') == 7
    assert "ssh-add '/keys/key with spaces'" in calls[0][1]
    assert calls[0][0] == host and calls[0][2] == {'interactive': True}
