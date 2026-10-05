"""Install and configure the standalone worker on hosts."""
from __future__ import annotations

import json
import os
import shlex
from importlib.resources import as_file

from fleet.transport import FleetError, Host

# Agents are often only on PATH in interactive login shells (nvm, pyenv), so ask those first.
# Each shell may set up a different PATH (e.g. nvm only in .bashrc), so every one is asked.
DETECT_SCRIPT = r"""
probe='echo "PATH=$PATH"; echo "claude=$(command -v claude)"; echo "codex=$(command -v codex)"'
for shell in zsh bash; do
  command -v $shell >/dev/null || continue
  $shell -lic "$probe" 2>/dev/null </dev/null | grep -E '^(PATH|claude|codex)='
done
eval "$probe"
"""


# Spelled out rather than $XDG_RUNTIME_DIR, which some sshd/PAM setups leave unset.
AGENT_SOCKET = "/run/user/$(id -u)/fleet-ssh-agent.sock"


def merge_detected(output: str) -> dict[str, str | None]:
    """First hit for each agent wins; its directory goes on PATH so `#!/usr/bin/env node` resolves."""
    found: dict[str, str] = {}
    first_path = ""
    for line in output.splitlines():
        name, _, value = line.partition("=")
        if name == "PATH":
            first_path = first_path or value
        elif value.startswith("/") and name not in found:
            found[name] = value
    directories = list(dict.fromkeys(os.path.dirname(binary) for binary in found.values()))
    path = ":".join(directories + [entry for entry in first_path.split(":") if entry and entry not in directories])
    return {"path": path, "claude": found.get("claude"), "codex": found.get("codex")}


class HostSetup:
    def __init__(self, transport):
        self.transport = transport

    def install(self, name: str) -> tuple[Host, dict, str]:
        """Copy fleetd to the host and record where its agent binaries live."""
        host = self.transport.host_by_name(name)
        destination = self.transport.REMOTE_FLEETD_PATH
        self.transport.run_shell(host, "mkdir -p ~/.local/share/fleet", check=True, capture_output=True)
        with as_file(self.transport.LOCAL_FLEETD_SOURCE) as source:
            self.transport.rsync([str(source)],
                                 host.rsync_target(os.path.expanduser(destination) if host.is_local else destination), host)
        detected = self.transport.run_shell(host, DETECT_SCRIPT, capture_output=True, text=True, timeout=60).stdout
        agent_socket = self.transport.run_shell(host, f"test -S {AGENT_SOCKET} && echo {AGENT_SOCKET}",
                                               capture_output=True, text=True, timeout=20).stdout.strip()
        if host.is_local and not agent_socket:
            agent_socket = os.environ.get("SSH_AUTH_SOCK", "")  # this machine's own agent already holds the keys
        settings = {**merge_detected(detected), "ssh_auth_sock": agent_socket or None}
        report = self.transport.call(host, ["configure", json.dumps(settings)])
        return host, report, agent_socket

    def hooks(self, name: str, action: str) -> tuple[Host, dict]:
        """Add fleet's hooks to every interactive Claude session on a host, or take them out again."""
        host = self.transport.host_by_name(name)
        try:
            report = self.transport.call(host, ["session-hooks", action])
        except FleetError as error:
            raise FleetError(f"{error} — if fleetd there predates session hooks, run: fleet install {host.name}") from error
        return host, report

    def unlock(self, name: str, key_path: str | None) -> int:
        host = self.transport.host_by_name(name)
        key = f" {shlex.quote(key_path)}" if key_path else ""
        command = f"SSH_AUTH_SOCK={AGENT_SOCKET} ssh-add{key} && SSH_AUTH_SOCK={AGENT_SOCKET} ssh-add -l"
        return self.transport.run_shell(host, command, interactive=True).returncode
