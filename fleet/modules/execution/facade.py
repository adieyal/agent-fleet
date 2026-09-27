from typing import Callable, TYPE_CHECKING
from datetime import datetime, timezone

from .application import link, observe, unavailable
from .application.delivery import queue, retry as retry_delivery

from .application.ports import ExecutionRepository, InputSender
from .domain import Action, Claim, Delivery, DispatchResult, JobObservation, Run
from .domain.activity import ACTION_FRESHNESS_SECONDS, classify_activity
from fleet.modules.attention import AttentionItem
from .application.dispatch import dispatch, retry, resolve_unknown
from .application.worker import deliver
from fleet.modules.work import WorkFacade
from fleet.modules.authority import AuthorityRejected

if TYPE_CHECKING:
    from fleet.modules.decisions import Decision


class ExecutionFacade:
    def __init__(self, repository: ExecutionRepository, work: WorkFacade,
                 prepare_dispatch: Callable[[], object] | None = None, *, send: InputSender | None = None,
                 authority=None, clock: Callable[[], datetime] | None = None) -> None:
        self.repository, self.work = repository, work
        self.send = send
        self.prepare_dispatch = prepare_dispatch
        self.authority = authority
        self.clock = clock if clock is not None else lambda: datetime.now(timezone.utc)

    classify_activity = staticmethod(classify_activity)

    def run_activity(self, run: Run) -> dict:
        observed = run.action_observed_at
        freshness = "unknown"
        if run.current_action is not None and observed is not None:
            freshness = "stale" if (self.clock() - observed).total_seconds() > ACTION_FRESHNESS_SECONDS else "current"
        return {"action_glyph": run.current_action,
                "action_observed_at": observed.isoformat() if observed is not None else None,
                "action_freshness": freshness}

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

    def get_action(self, identity: str) -> Action:
        return self.repository.get_action(identity)

    def get_run(self, identity: str) -> Run:
        return self.repository.get_run(identity)

    def find_run(self, host: str, job: str) -> Run | None:
        return self.repository.find(host, job)

    def dispatch(self, work_item: str | None, *, activation: str | None = None, **arguments) -> DispatchResult:
        if 'authorization' in arguments:
            raise AuthorityRejected('supply an activation ID')
        if activation is not None:
            if self.authority is None:
                raise AuthorityRejected('activation authority is not configured')
            arguments['authorization'] = self.authority().require('dispatch', work_item,
                actor=arguments['actor'], activation=activation)
        if self.prepare_dispatch is not None:
            self.prepare_dispatch()
        return dispatch(self.repository, work_item, **arguments)

    def deliver(self, run: Run, call: Callable, push: Callable, *, reconcile: bool = False) -> dict:
        return deliver(self.repository, run, call, push, reconcile=reconcile)

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
