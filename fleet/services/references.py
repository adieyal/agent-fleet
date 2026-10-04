"""Resolve retained identities and worker references."""
from __future__ import annotations

import re
from typing import Any

from fleet.identifiers import resolve_prefix
from fleet.transport import FleetError, Host


class References:
    def __init__(self, services, transport, workspace, attention):
        self.services, self.transport = services, transport
        self.workspace, self.attention = workspace, attention

    def job(self, reference: str) -> tuple[Host, str]:
        """Expand job prefixes locally first; query workers only for identities not yet retained."""
        host_name, job_id = reference.split(":", 1) if ":" in reference else (None, reference)
        if not job_id:
            raise FleetError("job id must not be empty; use host:id")
        hosts = [self.transport.host_by_name(host_name)] if host_name is not None else self.transport.configured_hosts()
        # A full worker UUID already names its directory; no listing is needed.
        if host_name is not None and re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", job_id):
            return hosts[0], job_id
        by_name = {host.name: host for host in hosts}
        identities = self.services.execution.job_identities(host_name)
        local = [(by_name[name], identity) for name, identity in identities
                 if name in by_name and identity.startswith(job_id)]
        exact = [match for match in local if match[1] == job_id]
        matches = exact or local
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            scope = f" on {host_name}" if host_name is not None else "; use host:id"
            raise FleetError(f"'{job_id}' matches {len(matches)} jobs{scope}")
        if host_name is not None:
            try:
                jobs = self.transport.call(hosts[0], ["ls", "--all", "--events", "0"]).get("jobs", [])
            except FleetError as error:
                raise FleetError(f"cannot resolve '{reference}': {error}") from error
            remote = [(hosts[0], job["id"]) for job in jobs if job["id"].startswith(job_id)]
        else:
            reports = self.transport.gather(hosts, ["ls", "--all", "--events", "0"])
            errors = [report.error for report in reports if report.error]
            if errors:
                raise FleetError(f"cannot resolve '{reference}': " + "; ".join(errors))
            remote = [(report.host, job["id"]) for report in reports for job in report.jobs
                      if job["id"].startswith(job_id)]
        exact = [match for match in remote if match[1] == job_id]
        matches = exact or remote
        if len(matches) != 1:
            scope = f" on {host_name}" if host_name is not None else "; use host:id"
            raise FleetError(f"'{job_id}' matches {len(matches)} jobs{scope}")
        return matches[0]

    def job_or_session(self, reference: str) -> tuple[Host, str]:
        """`host:id`, or a bare id of a job, else of an interactive session, searched on every host."""
        try:
            return self.job(reference)
        except FleetError:
            hosts = self.transport.configured_hosts()
            sessions = self.transport.gather_sessions(hosts)
            matches = [(host, session["id"]) for host in hosts for session in sessions.get(host.name, [])
                       if session["id"].startswith(reference)]
            if len(matches) != 1:
                raise
            return matches[0]

    def guidance(self, reference: str) -> tuple[str, str | None]:
        """(project, epic) for an epic ID, or (project, None) for a project ID or name."""
        work = self.services.work
        identities = [item.id for item in work.list()]
        if any(identity.startswith(reference) for identity in identities):
            reference = resolve_prefix(reference, identities, "work item")
        try:
            item = work.get(reference)
        except LookupError:
            return self.workspace().resolve_project(reference), None
        return item.project, item.id

    def work(self, reference: str) -> str:
        return resolve_prefix(reference, [item.id for item in self.services.work.list()], "work item")

    def attention_item(self, reference: str) -> str:
        return resolve_prefix(reference, [item.id for item in self.attention().list()], "attention item")

    def step_work(self, steps: list[dict[str, Any]]) -> None:
        for step in steps:
            if step.get("work_item") is not None:
                step["work_item"] = self.work(step["work_item"])
