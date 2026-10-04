import hashlib
import json
from dataclasses import replace
from uuid import uuid4

from ..domain import Action, Claim, DispatchResult, Run
from .ports import ExecutionRepository


def fingerprint(work_item: str | None, project: str, host: str, runtime: str, payload: dict) -> str:
    # Delivery bookkeeping must not turn a repeated dispatch request into a different command.
    payload = {key: value for key, value in payload.items()
               if key not in ('decisions_dispatch_at', 'decisions_dispatch_ids')}
    return hashlib.sha256(json.dumps([work_item, project, host, runtime, payload], sort_keys=True).encode()).hexdigest()


def require_step_work(work, work_item: str, project: str | None) -> None:
    """A step may serve its own work item, but only one that exists in the job's project."""
    item = work.get(work_item)
    if project is not None and item.project != project:
        raise ValueError(f"step work item {work_item} is in another project")


def claim(transaction: ExecutionRepository, action: Action, host: str, runtime: str, actor: str,
          key: str, digest: str, remote_job_id: str | None = None) -> DispatchResult:
    transaction.workspace.require_claims_allowed(action.project, host)
    active = next((claim for claim in transaction.claims() if claim.action == action.id and claim.active), None)
    if active is not None:
        run = next(run for run in transaction.runs() if run.id == active.run)
        transaction.save_request(key, digest, run.id, actor)
        return DispatchResult(run, False)
    identity = str(uuid4())
    run = Run(identity, action.id, host, identity if remote_job_id is None else remote_job_id,
              runtime, "unknown outcome", None, None, None, None)
    transaction.save_run(run, actor)
    transaction.save_claim(Claim(action.id, run.id, True), actor)
    transaction.save_request(key, digest, run.id, actor)
    return DispatchResult(run, True)


def dispatch(repository: ExecutionRepository, work_item: str | None, *, host: str, runtime: str, payload: dict,
             actor: str, reason: str, idempotency_key: str, project: str | None = None,
             remote_job_id: str | None = None, authorization=None, guidance: dict | None = None) -> DispatchResult:
    if not all(value.strip() for value in (host, runtime, actor, reason, idempotency_key)):
        raise ValueError("host, runtime, actor, reason and idempotency key are required")
    if not payload["cwd"].strip():
        raise ValueError("cwd is required")
    with repository.transaction() as transaction:
        if work_item is not None:
            project = transaction.work.get(work_item).project
        if project is None:
            raise ValueError("project or work item is required")
        for step in payload.get("steps") or []:
            if isinstance(step, dict) and step.get("work_item") is not None:
                require_step_work(transaction.work, step["work_item"], project)
        # Fingerprints of unguided dispatches stay as they were.
        fingerprinted = payload if guidance is None else dict(payload, guidance=guidance)
        digest = fingerprint(work_item, project, host, runtime, fingerprinted)
        existing = transaction.request(idempotency_key, digest)
        if existing is not None:
            return DispatchResult(existing, False)
        transaction.workspace.require_claims_allowed(project, host)
        action = Action(str(uuid4()), work_item, "dispatch", reason, actor, idempotency_key,
                        digest, project, payload,
                        None if authorization is None else authorization.id,
                        None if authorization is None else authorization.mandate_version, guidance)
        transaction.save_action(action, actor)
        return claim(transaction, action, host, runtime, actor, idempotency_key, digest, remote_job_id)


def retry(repository: ExecutionRepository, run_id: str, *, actor: str, idempotency_key: str) -> DispatchResult:
    if not actor.strip() or not idempotency_key.strip():
        raise ValueError("actor and idempotency key are required")
    with repository.transaction() as transaction:
        run = next((run for run in transaction.runs() if run.id == run_id), None)
        if run is None:
            raise LookupError(f"no run '{run_id}'")
        action = next(action for action in transaction.actions() if action.id == run.action)
        digest = hashlib.sha256(json.dumps(["retry", run_id]).encode()).hexdigest()
        existing = transaction.request(idempotency_key, digest)
        if existing is not None:
            return DispatchResult(existing, False)
        if run.status not in ("succeeded", "failed", "stopped"):
            raise ValueError(f"cannot retry run with {run.status}; resolve-unknown first")
        if action.payload is None:
            raise ValueError("linked run has no dispatch payload")
        return claim(transaction, action, run.host, run.runtime, actor, idempotency_key, digest)


def resolve_unknown(repository: ExecutionRepository, run_id: str, actor: str) -> Run:
    if not actor.strip():
        raise ValueError("actor is required")
    with repository.transaction() as transaction:
        run = next((run for run in transaction.runs() if run.id == run_id), None)
        if run is None:
            raise LookupError(f"no run '{run_id}'")
        if run.reason == "resolved unknown":
            return run
        if run.status != "unknown outcome":
            raise ValueError("run does not have an unknown outcome")
        updated = replace(run, status="stopped", reason="resolved unknown")
        transaction.update(updated, actor)
        transaction.release_claim(run.id, actor)
        return updated
