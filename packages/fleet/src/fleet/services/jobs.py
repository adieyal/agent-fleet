"""Job operations coordinated with retained execution state."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fleet.ingestion import observe_runs
from fleet.projections.workspace import resolve as resolve_label
from fleet.transport import FleetError, Host, HostReport


def listing_arguments(*, since: float, all_jobs: bool) -> list[str]:
    arguments = ["ls", "--since-hours", str(since)]
    return arguments + (["--all"] if all_jobs else [])


class Jobs:
    def __init__(self, services, transport, workspace, attention, references, context):
        self.services, self.transport = services, transport
        self.workspace, self.attention = workspace, attention
        self.references, self.context = references, context

    def selected_hosts(self, names: list[str] | None = None) -> list[Host]:
        hosts = self.transport.configured_hosts()
        if names:
            hosts = [host for host in hosts if host.name in names]
        if not hosts:
            raise FleetError("no hosts configured — fleet host add <name> --ssh <target>")
        return hosts

    def listing(self, hosts: list[Host], *, since: float, all_jobs: bool,
                sessions: bool, project: str | None) -> tuple[list[HostReport], dict[str, list[dict[str, Any]]]]:
        arguments = listing_arguments(since=since, all_jobs=all_jobs)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = pool.submit(self.transport.gather, hosts, arguments)
            asked_sessions = pool.submit(self.transport.gather_sessions, hosts) if sessions else None
            reports = jobs.result()
            by_host = asked_sessions.result() if asked_sessions else {}
        if project:
            workspace = self.workspace()
            identity = workspace.resolve_project(project)
            links = {(link.host, link.label) for link in workspace.registry().get(identity).links}
            for report in reports:
                report.jobs = [job for job in report.jobs if (report.host.name, job.get("project")) in links]
            by_host = {name: [session for session in host_sessions if (name, session.get("project")) in links]
                       for name, host_sessions in by_host.items()}
        return reports, by_host

    def waiting_step(self, host: Host, job_id: str) -> int | None:
        """Observe the unanswered blocked question and return its step index, if any."""
        job = self.transport.call(host, ["show", job_id, "--events", "0"])
        waiting = next((step["index"] for step in job["steps"]
                        if step["status"] == "blocked" and step.get("answered_by") is None), None)
        if waiting is not None:
            # Observe the actual question before answering, even if the stream has not yet
            # created it. Its resolved occurrence then also covers late blocked snapshots.
            observed = resolve_label(self.workspace().registry(), host.name, job)
            self.attention().observe({"name": host.name, "ok": True, "jobs": [observed], "sessions": []},
                                     subjects={f"job:{host.name}:{job_id}"})
        return waiting

    def answer_waiting_step(self, host: Host, job_id: str, step: int, steps: list[dict[str, Any]], actor: str) -> str:
        """Add the steps as the answer to the waiting step, as the deck does: they run next, and the open attention
        item for the step is resolved. Keyed like the deck's answer, so a retried add queues nothing more."""
        attention = self.attention()
        item = next((item for item in attention.list() if item.state != "resolved" and item.stream_context is not None
                     and item.stream_context.blocked_step and (item.stream_context.host, item.stream_context.owner_id,
                                                               item.stream_context.step) == (host.name, job_id, step)),
                    None)
        payload = json.dumps([{"title": f"Answer to step {step + 1}", **added} for added in steps])
        key = f"{item.id}:answer" if item is not None else (
            f"cli:{job_id}:{step}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}")
        result = self.transport.call(host, ["add", job_id, "--steps-file", "/dev/stdin", "--schema-version", "1",
                                       "--key", key, "--answers", str(step)], stdin_text=payload)
        if not isinstance(result, dict) or (result.get("status"), result.get("answers")) != ("applied", step):
            raise FleetError("worker did not confirm the answer")
        continuation = result["steps"][0]
        details = f"answered; step {step + 1} continues as step {continuation + 1}"
        # The stream can create the blocker while the worker call is in flight.
        # Resolve from confirmed receipt, including items absent from the initial lookup.
        for waiting in attention.list():
            context = waiting.stream_context
            if (waiting.state != "resolved" and context is not None and context.blocked_step
                    and (context.host, context.owner_id, context.step) == (host.name, job_id, step)):
                attention.resolve(waiting.id, details=details, actor=actor)
        return details

    def start(self, host: Host, job_id: str) -> dict:
        execution = self.services.execution
        run = next((run for run in execution.runs() if (run.host, run.remote_job_id) == (host.name, job_id)), None)

        def call(fleetd_arguments, stdin):
            return self.transport.call(host, fleetd_arguments, stdin_text=stdin)

        try:
            job = call(["start", job_id], None) if run is None else execution.start(run, call)
        except (ValueError, LookupError) as error:
            raise FleetError(str(error)) from error
        return job

    def remove(self, host: Host, job_id: str, *, force: bool) -> dict:
        job = self.transport.call(host, ["show", job_id, "--events", "0"])
        execution = self.services.execution
        registry = self.workspace().registry()
        observe_runs(execution, self.services.library, {"name": host.name, "ok": True, "jobs": {job_id: job}},
                     project_of=lambda value: resolve_label(registry, host.name, value)["project_id"])
        self.transport.keep_run_trace(execution, host, job)
        removed = self.transport.call(host, ["rm", job_id, *(["--force"] if force else [])])
        execution.removed(host.name, job_id, at=removed.get("removed_at"))
        return removed

    def add(self, host: Host, job_id: str, steps: list[dict[str, Any]], *, context, retry: bool, no_answer: bool, actor: str) -> tuple[dict | None, str | None]:
        self.references.step_work(steps)
        named = [step["work_item"] for step in steps if step.get("work_item") is not None]
        if named:
            execution = self.services.execution
            try:
                for work_item in named:
                    execution.require_step_work(host.name, job_id, work_item)
            except (ValueError, LookupError) as error:
                raise FleetError(str(error)) from error
        if context:
            self.context.push(host, job_id, context)
        waiting = None if retry or no_answer else self.waiting_step(host, job_id)
        if waiting is not None:
            details = self.answer_waiting_step(host, job_id, waiting, steps, actor)
            return None, details
        fleetd_arguments = ["add", job_id, "--steps-file", "/dev/stdin"] + (["--retry"] if retry else [])
        job = self.transport.call(host, fleetd_arguments, stdin_text=json.dumps(steps))
        return job, None

    def edit_step(self, host: Host, job_id: str, step: int, prompt: str, *, actor: str) -> dict[str, Any]:
        """Replace an unstarted step's prompt; public step numbers start at one."""
        if isinstance(step, bool) or not isinstance(step, int) or step < 1:
            raise FleetError("step number must be a positive integer")
        if not prompt.strip():
            raise FleetError("step prompt must not be empty")
        if not actor.strip():
            raise FleetError("step edit actor must not be empty")
        result = self.transport.call(host, ["edit-step", job_id, str(step - 1), "--actor", actor], stdin_text=prompt)
        if not isinstance(result, dict) or any(result.get(key) != value for key, value in
                {"job": job_id, "step": step - 1, "status": "edited", "prompt": prompt}.items()):
            raise FleetError("worker did not confirm the step edit")
        return result

    def show(self, host: Host, job_id: str, *, events: int | None = None) -> dict:
        arguments = ["show", job_id] + (["--events", str(events)] if events is not None else [])
        return self.transport.call(host, arguments)

    def events(self, host: Host, job_id: str, *, lines: int, follow: bool):
        return self.transport.events(host, job_id, lines=lines, follow=follow)

    def attach(self, host: Host, job_id: str) -> None:
        job = self.show(host, job_id)
        self.transport.exec_shell(host, job["tmux"])

    def wait(self, pending: dict[str, tuple[Host, str]], *, step: int | None, timeout: float | None, any_job: bool):
        return self.transport.wait_jobs(pending, step=step, timeout=timeout, any_job=any_job)

    def result(self, host: Host, job_id: str, *, step: int | None) -> dict:
        arguments = ["result", job_id] + (["--step", str(step - 1)] if step else [])
        return self.transport.call(host, arguments)

    def cancel(self, host: Host, job_id: str, *, all_steps: bool) -> dict:
        return self.transport.call(host, ["cancel", job_id] + (["--all-steps"] if all_steps else []))

    def move(self, project: str, targets: list[tuple[Host, str]]):
        workspace = self.workspace()
        identity = workspace.resolve_project(project)
        moves = [(host, job_id, workspace.host_label(identity, host.name)) for host, job_id in targets]
        for host, job_id, label in moves:
            self.transport.call(host, ["mv", job_id, label])
            yield host, job_id, identity, label

    def host_reports(self):
        hosts = self.transport.configured_hosts()
        return self.transport.gather(hosts, ['ls']) if hosts else []

    def notification_reports(self, hosts):
        return self.transport.gather(hosts, ['ls', '--since-hours', '48'])
