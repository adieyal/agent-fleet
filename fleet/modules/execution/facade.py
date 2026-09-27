from .application import link, observe, unavailable
from .application.ports import ExecutionRepository
from .domain import Action, JobObservation, Run
from fleet.modules.work import WorkFacade


class ExecutionFacade:
    def __init__(self, repository: ExecutionRepository, work: WorkFacade) -> None:
        self.repository, self.work = repository, work

    def link(self, host: str, job: str, work_item: str, *, actor: str, runtime: str | None = None) -> Run:
        return link(self.repository, self.work, host, job, work_item, actor, runtime)

    def actions(self) -> list[Action]:
        return self.repository.actions()

    def observe(self, host: str, observation: JobObservation) -> Run | None:
        return observe(self.repository, host, observation)

    def unavailable(self, host: str) -> bool:
        return unavailable(self.repository, host)

    def runs(self) -> list[Run]:
        return self.repository.runs()
