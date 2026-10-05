"""Transfer and address job context."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fleet.transport import FleetError, Host


class Context:
    def __init__(self, records, transport):
        self.records, self.transport = records, transport

    def push(self, host: Host, job_id: str, paths: list[str]) -> None:
        missing = [path for path in paths if not os.path.exists(path)]
        if missing:
            raise FleetError(f"context not found: {', '.join(missing)}")
        remote_directory = f"~/.fleet/jobs/{job_id}/context/" if not host.is_local else os.path.expanduser(
            f"~/.fleet/jobs/{job_id}/context/")
        self.transport.rsync([os.path.abspath(path) for path in paths], host.rsync_target(remote_directory), host)

    def push_guided(self, host: Host, job_id: str, paths: list[str], guidance: dict | None) -> None:
        """Context paths plus the pinned constitution and charter, written as CONSTITUTION.md and CHARTER.md."""
        if guidance is None:
            self.push(host, job_id, paths)
            return
        with tempfile.TemporaryDirectory(prefix="fleet-guidance-") as directory:
            self.push(host, job_id, paths + self.records.write_guidance_files(guidance, directory))

    def locate(self, reference: str) -> tuple[str, bool]:
        """A context reference naming a local file, addressed as fleet://<this host>/<absolute path> so the deck can open
        it from any machine. Relative paths resolve against the working directory, then the calling job's directory.
        Anything else (a URL, a session, prose) is kept as given; a path-like reference that resolves nowhere is kept and
        warned about, since the deck will show it only as text."""
        if "://" in reference or reference.startswith(("session:", "job:")) or not reference.strip():
            return reference, False
        candidates = [Path(reference).expanduser()]
        if not candidates[0].is_absolute():
            candidates = [Path.cwd() / reference]
            if os.environ.get("FLEET_JOB_ID"):
                candidates.append(Path("~/.fleet/jobs").expanduser() / os.environ["FLEET_JOB_ID"] / reference)
        local = next((host for host in self.transport.configured_hosts() if host.is_local), None)
        for candidate in candidates:
            if candidate.is_file() and local is not None:
                return f"fleet://{local.name}{candidate.resolve()}", False
        warn = "/" in reference or reference.endswith((".md", ".json", ".txt", ".png"))
        return reference, warn

    def pull(self, host: Host, job_id: str, destination: str | None) -> Path:
        target = Path(destination or f"./fleet-{job_id}").resolve()
        target.mkdir(parents=True, exist_ok=True)
        source = f"~/.fleet/jobs/{job_id}/outbox/"
        self.transport.rsync([host.rsync_target(os.path.expanduser(source) if host.is_local else source)], str(target), host)
        return target
