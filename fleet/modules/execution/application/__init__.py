"""Link host-local jobs without changing accepted work."""

from uuid import uuid4
from dataclasses import replace

from .ports import ExecutionRepository
from ..domain import Action, JobObservation, Run
from fleet.modules.work import WorkFacade


def link(repository: ExecutionRepository, work: WorkFacade, host: str, job: str,
         work_item: str, actor: str, runtime: str | None) -> Run:
    if not actor.strip():
        raise ValueError("actor is required")
    work.get(work_item)
    with repository.transaction() as transaction:
        existing = transaction.find(host, job)
        if existing is not None:
            action = next(action for action in transaction.actions() if action.id == existing.action)
            if action.work_item != work_item:
                raise ValueError("job is already linked to another work item")
            return existing
        action = Action(str(uuid4()), work_item, "linked")
        run = Run(str(uuid4()), action.id, host, job, runtime, "unknown outcome", None, None, None, None)
        transaction.save(action, run, actor)
        return run


def observe(repository: ExecutionRepository, host: str, observation: JobObservation) -> Run | None:
    with repository.transaction() as transaction:
        run = transaction.find(host, observation.job)
        if run is None:
            return None
        updated = replace(run, status=observation.run_status(), reason="lost" if observation.status == "lost" else None,
                          runtime=observation.runtime, start=observation.start, end=observation.end,
                          last_observed=observation.observed_at)
        if updated != run:
            transaction.update(updated, "fleetd")
        if updated.status in ("succeeded", "failed", "stopped"):
            transaction.release_claim(updated.id, "fleetd")
        return updated


def unavailable(repository: ExecutionRepository, host: str) -> bool:
    changed = False
    with repository.transaction() as transaction:
        for run in transaction.runs():
            if run.host == host and run.status == "running":
                transaction.update(replace(run, status="unknown outcome", reason=None), "fleetd")
                changed = True
    return changed
