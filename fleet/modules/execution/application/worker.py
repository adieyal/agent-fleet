"""Deliver an intent and reconcile uncertain worker replies by run ID."""

import hashlib
import json
from collections.abc import Callable

from fleet.errors import FleetError
from . import observe
from .ports import ExecutionRepository
from ..domain import JobObservation, Run


def deliver(repository: ExecutionRepository, run: Run, call: Callable, push: Callable, *, reconcile: bool) -> dict:
    action = repository.get_action(run.action)
    payload = action.payload
    if payload is None:
        raise ValueError("linked run has no dispatch payload")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    identity = ["--run-id", run.id, "--fingerprint", digest, "--schema-version", "3"]

    def check(job: dict) -> dict:
        if (job["id"] != run.remote_job_id or job["run_id"] != run.id
                or job["schema_version"] != 3 or job["fingerprint"] != digest):
            raise FleetError("worker returned a different run; run outcome is unknown")
        observe(repository, run.host, JobObservation(job["id"], job["status"], run.runtime,
                                                    run.start, run.end, run.last_observed))
        return job

    def recover() -> dict:
        return check(call(["reconcile", run.id, *identity[2:]], None))

    if reconcile:
        job = recover()
    else:
        arguments = list(payload["arguments"])
        if "--id" in arguments:
            index = arguments.index("--id")
            del arguments[index:index + 2]
        arguments += ["--id", run.remote_job_id, *identity]
        try:
            job = check(call(arguments, json.dumps(payload["steps"])))
        except FleetError as error:
            try:
                job = recover()
            except FleetError:
                raise error
    if job["status"] == "queued" and job["start_requested"] is False:
        if payload["context"]:
            push(job["id"], payload["context"])
        if not payload["hold"]:
            try:
                job = check(call(["start", job["id"], *identity], None))
            except FleetError as error:
                try:
                    job = recover()
                except FleetError:
                    raise error
    return job
