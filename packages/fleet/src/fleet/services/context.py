"""Transfer and address job context."""
from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol
from pathlib import Path

from fleet.transport import FleetError, Host


class ContextFiles(Protocol):
    def exists(self, path: str) -> bool: ...
    def absolute(self, path: str) -> str: ...
    def expand_user(self, path: str) -> str: ...
    def temporary_directory(self, *, prefix: str) -> AbstractContextManager[str]: ...
    def find_reference(self, reference: str) -> Path | None: ...
    def create_directory(self, destination: str) -> Path: ...


class Context:
    def __init__(self, records, transport, files: ContextFiles):
        self.records, self.transport, self.files = records, transport, files

    def push(self, host: Host, job_id: str, paths: list[str]) -> None:
        missing = [path for path in paths if not self.files.exists(path)]
        if missing:
            raise FleetError(f"context not found: {', '.join(missing)}")
        remote_directory = f"~/.fleet/jobs/{job_id}/context/" if not host.is_local else self.files.expand_user(
            f"~/.fleet/jobs/{job_id}/context/")
        self.transport.rsync([self.files.absolute(path) for path in paths], host.rsync_target(remote_directory), host)

    def push_guided(self, host: Host, job_id: str, paths: list[str], guidance: dict | None) -> None:
        """Context paths plus the pinned constitution and charter, written as CONSTITUTION.md and CHARTER.md."""
        if guidance is None:
            self.push(host, job_id, paths)
            return
        with self.files.temporary_directory(prefix="fleet-guidance-") as directory:
            self.push(host, job_id, paths + self.records.write_guidance_files(guidance, directory))

    def locate(self, reference: str) -> tuple[str, bool]:
        """A context reference naming a local file, addressed as fleet://<this host>/<absolute path> so the deck can open
        it from any machine. Relative paths resolve against the working directory, then the calling job's directory.
        Anything else (a URL, a session, prose) is kept as given; a path-like reference that resolves nowhere is kept and
        warned about, since the deck will show it only as text."""
        if "://" in reference or reference.startswith(("session:", "job:")) or not reference.strip():
            return reference, False
        local = next((host for host in self.transport.configured_hosts() if host.is_local), None)
        candidate = self.files.find_reference(reference)
        if candidate is not None and local is not None:
            return f"fleet://{local.name}{candidate}", False
        warn = "/" in reference or reference.endswith((".md", ".json", ".txt", ".png"))
        return reference, warn

    def pull(self, host: Host, job_id: str, destination: str | None) -> Path:
        target = self.files.create_directory(destination or f"./fleet-{job_id}")
        source = f"~/.fleet/jobs/{job_id}/outbox/"
        self.transport.rsync([host.rsync_target(self.files.expand_user(source) if host.is_local else source)], str(target), host)
        return target
