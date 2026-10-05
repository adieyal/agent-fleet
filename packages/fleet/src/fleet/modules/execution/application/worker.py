"""Deliver an intent and reconcile uncertain worker replies by run ID."""

import hashlib
import json
from collections.abc import Callable

from fleet.errors import FleetError
from . import observe
from .ports import ExecutionRepository
from ..domain import JobObservation, Run, Usage


def dispatch_identity(repository: ExecutionRepository, run: Run) -> list[str]:
    """The arguments that let the worker check a request is for this run and its unchanged payload."""
    payload = repository.get_action(run.action).payload
    if payload is None:
        raise ValueError("linked run has no dispatch payload")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return ["--run-id", run.id, "--fingerprint", digest, "--schema-version", "4"]


def start(repository: ExecutionRepository, run: Run, call: Callable) -> dict:
    """Start a job created with --hold."""
    job = call(["start", run.remote_job_id, *dispatch_identity(repository, run)], None)
    observe(repository, run.host, JobObservation(job["id"], job["status"], run.runtime,
                                                 run.start, run.end, run.last_observed, Usage.from_worker(job)))
    return job


def deliver(repository: ExecutionRepository, run: Run, call: Callable, push: Callable, *, reconcile: bool) -> dict:
    action = repository.get_action(run.action)
    payload = action.payload
    identity = dispatch_identity(repository, run)
    digest = identity[3]

    def check(job: dict) -> dict:
        if (job["id"] != run.remote_job_id or job["run_id"] != run.id
                or job["schema_version"] != 4 or job["fingerprint"] != digest):
            raise FleetError("worker returned a different run; run outcome is unknown")
        observe(repository, run.host, JobObservation(job["id"], job["status"], run.runtime,
                                                    run.start, run.end, run.last_observed, Usage.from_worker(job)))
        return job

    def create() -> dict:
        arguments = list(payload["arguments"])
        if "--id" in arguments:
            index = arguments.index("--id")
            del arguments[index:index + 2]
        arguments += ["--id", run.remote_job_id, *identity]
        return check(call(arguments, json.dumps(payload["steps"])))

    def recover() -> dict:
        job = call(["reconcile", run.id, "--fingerprint", digest, "--schema-version", "4"], None)
        if job["status"] == "absent":
            if (job["schema_version"] != 4 or job["run_id"] != run.id
                    or job["fingerprint"] != digest):
                raise FleetError("worker returned a different run; run outcome is unknown")
            return create()
        return check(job)

    if reconcile:
        job = recover()
    else:
        try:
            job = create()
        except FleetError as error:
            try:
                job = recover()
            except FleetError:
                raise error
    if job["status"] == "queued" and job["start_requested"] is False:
        if payload["context"] or action.guidance is not None:
            push(job["id"], payload["context"] or [], action.guidance)
        if not payload["hold"]:
            try:
                job = check(call(["start", job["id"], *identity], None))
            except FleetError as error:
                try:
                    job = recover()
                except FleetError:
                    raise error
    return job
