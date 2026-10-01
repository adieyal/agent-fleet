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
            if action.source == "observed" and action.work_item is None:
                project = work.get(work_item).project
                if action.project is not None and action.project != project:
                    registry = transaction.workspace.registry()
                    raise ValueError(f"job project {registry.get(action.project).name} ({action.project}) differs from "
                                     f"work project {registry.get(project).name} ({project})")
                transaction.update_action(replace(action, work_item=work_item, project=project), actor)
                return existing
            if action.work_item != work_item:
                raise ValueError("job is already linked to another work item")
            return existing
        action = Action(str(uuid4()), work_item, "linked")
        run = Run(str(uuid4()), action.id, host, job, runtime, "unknown outcome", None, None, None, None)
        transaction.save(action, run, actor)
        return run


def record_observed(repository: ExecutionRepository, host: str, job: dict,
                    project: str | None) -> Run:
    """Record an externally observed job once, without reserving a claim."""
    with repository.transaction() as transaction:
        existing = transaction.find(host, job["id"])
        if existing is not None:
            fields = {field: job[key] for field, key in (
                ("label", "project"), ("title", "description"), ("cwd", "cwd"),
                ("workspace", "workspace"), ("workspace_reason", "workspace_reason")) if key in job}
            updated = replace(existing, **fields)
            if updated != existing:
                transaction.update(updated, "fleetd")
            return updated
        action = Action(str(uuid4()), None, "observed", actor="fleetd", project=project)
        run = Run(job.get("run_id") or str(uuid4()), action.id, host, job["id"], job.get("agent"),
                  "unknown outcome", None, None, None, None, label=job.get("project"),
                  title=job.get("description"), cwd=job.get("cwd"), workspace=job.get("workspace"),
                  workspace_reason=job.get("workspace_reason"))
        transaction.save(action, run, "fleetd")
        return run


def assign_label(repository: ExecutionRepository, host: str, label: str, project: str, actor: str) -> int:
    """Assign previously unregistered observed jobs to their label's newly linked project."""
    if not actor.strip():
        raise ValueError("actor is required")
    with repository.transaction() as transaction:
        transaction.workspace.registry().get(project)
        actions = {action.id: action for action in transaction.actions()}
        count = 0
        for run in transaction.runs():
            action = actions[run.action]
            if run.host == host and run.label == label and action.source == "observed" and action.project is None:
                transaction.update_action(replace(action, project=project), actor)
                count += 1
        return count


def observe(repository: ExecutionRepository, host: str, observation: JobObservation) -> Run | None:
    with repository.transaction() as transaction:
        run = transaction.find(host, observation.job)
        if run is None:
            return None
        updated = replace(run, status=observation.run_status(), reason=observation.status if observation.status in ("lost", "blocked") else None,
                          runtime=observation.runtime, start=observation.start, end=observation.end,
                          last_observed=observation.observed_at, usage=observation.usage)
        if observation.current_action is not None and observation.action_observed_at is not None:
            if run.action_observed_at is None or observation.action_observed_at >= run.action_observed_at:
                updated = replace(updated, current_action=observation.current_action,
                                  action_observed_at=observation.action_observed_at)
        if observation.step_work is not None:
            updated = replace(updated, step_work=observation.step_work)
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
