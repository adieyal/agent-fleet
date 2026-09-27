from typing import Callable, TYPE_CHECKING

from .application import link, observe, unavailable
from .application.delivery import queue, retry as retry_delivery

from .application.ports import ExecutionRepository, InputSender
from .domain import Action, Claim, Delivery, DispatchResult, JobObservation, Run
from fleet.modules.attention import AttentionItem
from .application.dispatch import dispatch, retry, resolve_unknown
from fleet.modules.work import WorkFacade

if TYPE_CHECKING:
    from fleet.modules.decisions import Decision


class ExecutionFacade:
    def __init__(self, repository: ExecutionRepository, work: WorkFacade,
                 prepare_dispatch: Callable[[], object] | None = None, *, send: InputSender | None = None) -> None:
        self.repository, self.work = repository, work
        self.send = send
        self.prepare_dispatch = prepare_dispatch

    def queue_answer(self, item: AttentionItem, decision: "Decision") -> None:
        queue(self.repository, item, decision)

    def deliveries(self) -> list[Delivery]:
        return self.repository.deliveries()

    def retry_deliveries(self, host: str | None = None, *, decision: str | None = None) -> None:
        if self.send is None:
            raise RuntimeError("input transport is not configured")
        retry_delivery(self.repository, self.work, self.send, host, decision)

    def link(self, host: str, job: str, work_item: str, *, actor: str, runtime: str | None = None) -> Run:
        return link(self.repository, self.work, host, job, work_item, actor, runtime)

    def actions(self) -> list[Action]:
        return self.repository.actions()

    def dispatch(self, work_item: str | None, **arguments) -> DispatchResult:
        if self.prepare_dispatch is not None:
            self.prepare_dispatch()
        return dispatch(self.repository, work_item, **arguments)

    def retry(self, run: str, *, actor: str, idempotency_key: str) -> DispatchResult:
        return retry(self.repository, run, actor=actor, idempotency_key=idempotency_key)

    def resolve_unknown(self, run: str, *, actor: str) -> Run:
        return resolve_unknown(self.repository, run, actor)

    def claims(self) -> list[Claim]:
        return self.repository.claims()

    def observe(self, host: str, observation: JobObservation) -> Run | None:
        return observe(self.repository, host, observation)

    def unavailable(self, host: str) -> bool:
        return unavailable(self.repository, host)

    def runs(self) -> list[Run]:
        return self.repository.runs()
