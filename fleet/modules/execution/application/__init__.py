"""Link host-local jobs without changing accepted work."""

from uuid import uuid4

from .ports import ExecutionRepository
from ..domain import Action, Run
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
