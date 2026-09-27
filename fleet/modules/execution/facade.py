from .application import link, observe, unavailable
from .application.delivery import queue, retry
from typing import TYPE_CHECKING

from .application.ports import ExecutionRepository, InputSender
from .domain import Action, Delivery, JobObservation, Run
from fleet.modules.attention import AttentionItem
from fleet.modules.work import WorkFacade

if TYPE_CHECKING:
    from fleet.modules.decisions import Decision


class ExecutionFacade:
    def __init__(self, repository: ExecutionRepository, work: WorkFacade, send: InputSender | None = None) -> None:
        self.repository, self.work = repository, work
        self.send = send

    def queue_answer(self, item: AttentionItem, decision: "Decision") -> None:
        queue(self.repository, item, decision)

    def deliveries(self) -> list[Delivery]:
        return self.repository.deliveries()

    def retry_deliveries(self, host: str | None = None, *, decision: str | None = None) -> None:
        if self.send is None:
            raise RuntimeError("input transport is not configured")
        retry(self.repository, self.work, self.send, host, decision)

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
