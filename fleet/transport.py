"""Talking to fleetd on each host: over ssh, or directly for the local machine."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REMOTE_FLEETD_PATH = "~/.local/share/fleet/fleetd.py"
LOCAL_FLEETD_SOURCE = Path(__file__).parent / "remote" / "fleetd.py"
SSH_OPTIONS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=6",
    "-o", "ControlMaster=auto",
    "-o", f"ControlPath={Path.home() / '.ssh'}/fleet-%C",
    "-o", "ControlPersist=10m",
]


class FleetError(Exception):
    pass


@dataclass(frozen=True)
class Host:
    name: str
    ssh_target: str | None
    python: str = "python3"

    @property
    def is_local(self) -> bool:
        return self.ssh_target is None

    def fleetd_command(self, arguments: list[str]) -> list[str]:
        fleetd = [self.python, os.path.expanduser(REMOTE_FLEETD_PATH) if self.is_local else REMOTE_FLEETD_PATH]
        if self.is_local:
            return fleetd + arguments
        remote_command = " ".join([fleetd[0], fleetd[1]] + [shlex.quote(argument) for argument in arguments])
        return ["ssh", *SSH_OPTIONS, self.ssh_target, remote_command]

    def shell_command(self, command: str, *, interactive: bool = False) -> list[str]:
        if self.is_local:
            return ["bash", "-c", command]
        return ["ssh", *(["-t"] if interactive else []), *SSH_OPTIONS, self.ssh_target, command]

    def rsync_target(self, path: str) -> str:
        return path if self.is_local else f"{self.ssh_target}:{path}"


@dataclass
class HostReport:
    host: Host
    jobs: list[dict[str, Any]]
    error: str | None = None


def config_path() -> Path:
    if "FLEET_CONFIG" in os.environ:
        return Path(os.environ["FLEET_CONFIG"])
    return Path.home() / ".config" / "fleet" / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if path.exists():
        return json.loads(path.read_text())
    return {"hosts": {}}


def save_config(config: dict[str, Any]) -> None:
    import tempfile
    from fleet.composition import open_workspace

    open_workspace()
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = {key: value for key, value in config.items() if key not in ("projects", "capacity")}
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
        temporary.write(json.dumps(settings, indent=2) + "\n")
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary.name, path)


def configured_hosts() -> list[Host]:
    return [Host(name, entry.get("ssh"), entry.get("python", "python3"))
            for name, entry in load_config().get("hosts", {}).items()]


def host_by_name(name: str) -> Host:
    for host in configured_hosts():
        if host.name == name:
            return host
    raise FleetError(f"unknown host '{name}' — add it with: fleet host add {name} --ssh <target>")


def ensure_master(host: Host) -> None:
    """Start the shared ssh connection detached from our pipes.

    A master spawned implicitly by ControlMaster=auto inherits the caller's
    stdout/stderr and keeps them open, so capture_output would block until
    ControlPersist expires.
    """
    if host.is_local:
        return
    control = ["-o", f"ControlPath={Path.home() / '.ssh'}/fleet-%C"]
    check = subprocess.run(["ssh", *control, "-O", "check", host.ssh_target], capture_output=True)
    if check.returncode != 0:
        subprocess.run(["ssh", *SSH_OPTIONS, "-o", "ControlMaster=yes", "-M", "-N", "-f", host.ssh_target],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


def call(host: Host, arguments: list[str], *, stdin_text: str | None = None, timeout: float | None = 30) -> Any:
    """Run a fleetd command and return its parsed JSON output."""
    ensure_master(host)
    try:
        completed = subprocess.run(host.fleetd_command(arguments), input=stdin_text, capture_output=True,
                                   text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise FleetError(f"{host.name}: timed out after {timeout}s") from error
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        detail = completed.stderr.strip().splitlines()[-1:] or [f"exit {completed.returncode}"]
        raise FleetError(f"{host.name}: {detail[0]}")
    document = json.loads(lines[-1])
    if isinstance(document, dict) and "error" in document:
        raise FleetError(f"{host.name}: {document['error']}")
    return document


def gather(hosts: list[Host], arguments: list[str]) -> list[HostReport]:
    """Run `fleetd ls …` on every host in parallel; unreachable hosts become error reports."""
    def one(host: Host) -> HostReport:
        try:
            return HostReport(host, call(host, arguments, timeout=20)["jobs"])
        except (FleetError, ValueError) as error:
            return HostReport(host, [], str(error))

    if not hosts:
        return []
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        return list(pool.map(one, hosts))


def gather_sessions(hosts: list[Host]) -> dict[str, list[dict[str, Any]]]:
    """Live interactive CLI sessions per host name, from `fleetd sessions`.

    A host whose fleetd predates the command (or that is unreachable, which `gather`
    already reports) contributes no sessions.
    """
    def one(host: Host) -> list[dict[str, Any]]:
        try:
            return call(host, ["sessions"], timeout=20).get("sessions", [])
        except (FleetError, ValueError, AttributeError):
            return []

    if not hosts:
        return {}
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        return dict(zip((host.name for host in hosts), pool.map(one, hosts)))


def repository_remotes(host: Host, directories: list[str]) -> dict[str, list[str]]:
    """Git remote URLs of each directory on the host; a directory outside git has none."""
    script = "".join(f"git -C {shlex.quote(directory)} config --get-regexp '^remote\\..*\\.url$' 2>/dev/null"
                     f" | while read -r _ url; do printf '%s\\t%s\\n' {shlex.quote(directory)} \"$url\"; done\n"
                     for directory in directories)
    ensure_master(host)
    try:
        completed = subprocess.run(host.shell_command(script), capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired as error:
        raise FleetError(f"{host.name}: timed out reading repository remotes") from error
    if completed.returncode != 0:
        raise FleetError(f"{host.name}: could not read repository remotes: {completed.stderr.strip()}")
    remotes: dict[str, list[str]] = {directory: [] for directory in directories}
    for line in completed.stdout.splitlines():
        directory, _, url = line.partition("\t")
        if directory in remotes and url:
            remotes[directory].append(url)
    return remotes


def rsync(sources: list[str], destination: str, host: Host) -> None:
    ensure_master(host)
    command = ["rsync", "-a", *sources, destination]
    if not host.is_local:
        command[1:1] = ["-e", " ".join(["ssh", *SSH_OPTIONS])]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise FleetError(f"rsync failed: {completed.stderr.strip()}")
