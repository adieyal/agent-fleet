"""Host values and command descriptions; no process execution."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REMOTE_FLEETD_PATH = "~/.local/share/fleet/fleetd.py"
SSH_OPTIONS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=6",
    "-o",
    "ControlMaster=auto",
    "-o",
    f"ControlPath={Path.home() / '.ssh'}/fleet-%C",
    "-o",
    "ControlPersist=10m",
]


@dataclass(frozen=True)
class Host:
    name: str
    ssh_target: str | None
    python: str = "python3"
    control_path: str | None = None

    @property
    def ssh_options(self) -> list[str]:
        return [
            f"ControlPath={self.control_path}"
            if self.control_path is not None and option.startswith("ControlPath=")
            else option
            for option in SSH_OPTIONS
        ]

    @property
    def is_local(self) -> bool:
        return self.ssh_target is None

    def fleetd_command(self, arguments: list[str]) -> list[str]:
        path = os.environ.get("FLEET_FLEETD_PATH", REMOTE_FLEETD_PATH)
        home = os.environ.get("FLEET_REMOTE_HOME")
        fleetd = [self.python, os.path.expanduser(path) if self.is_local else path]
        if self.is_local:
            prefix = (
                []
                if home is None
                else ["env", f"FLEET_HOME={os.path.expanduser(home)}"]
            )
            return prefix + fleetd + arguments
        if "FLEET_FLEETD_PATH" in os.environ:
            fleetd[1] = _remote_path(path)
        remote_command = " ".join(
            [fleetd[0], fleetd[1]] + [shlex.quote(argument) for argument in arguments]
        )
        if home is not None:
            remote_command = f"env FLEET_HOME={_remote_path(home)} " + remote_command
        return ["ssh", *self.ssh_options, self.ssh_target, remote_command]

    def shell_command(self, command: str, *, interactive: bool = False) -> list[str]:
        if self.is_local:
            return ["bash", "-c", command]
        return [
            "ssh",
            *(["-t"] if interactive else []),
            *self.ssh_options,
            self.ssh_target,
            command,
        ]

    def rsync_target(self, path: str) -> str:
        return path if self.is_local else f"{self.ssh_target}:{path}"


def _remote_path(path: str) -> str:
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


@dataclass
class HostReport:
    host: Host
    jobs: list[dict[str, Any]]
    error: str | None = None
