from typing import Callable, TYPE_CHECKING
from datetime import datetime, timezone

from .application import link, observe, unavailable
from .application.delivery import queue, retry as retry_delivery

from .application.answers import answer
from .application.permissions import grant
from .application.ports import AnswerSender, ExecutionRepository, GrantSender, InputSender, StepSender
from .application.dtos import StepRequest
from .domain import Action, Claim, Delivery, DispatchResult, JobObservation, Run
from .domain.activity import HOST_FRESHNESS_SECONDS, classify_activity
from fleet.modules.attention import AttentionItem, refusal_violation
from .application.dispatch import dispatch, require_step_work, retry, resolve_unknown
from .application.worker import deliver, start
from fleet.modules.work import WorkFacade
from fleet.modules.authority import AuthorityRejected

if TYPE_CHECKING:
    from fleet.modules.decisions import Decision


class ExecutionFacade:
    def __init__(self, repository: ExecutionRepository, work: WorkFacade,
                 prepare_dispatch: Callable[[], object] | None = None, *, send: InputSender | None = None,
                 grant: GrantSender | None = None, answer: AnswerSender | None = None,
                 authority=None, clock: Callable[[], datetime] | None = None,
                 step: StepSender | None = None) -> None:
        self.repository, self.work = repository, work
        self.send, self.grant, self.answer = send, grant, answer
        self.step = step
        self.prepare_dispatch = prepare_dispatch
        self.authority = authority
        self.clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self.host_observed: dict[str, datetime] = {}
        self.unreachable_hosts: set[str] = set()

    classify_activity = staticmethod(classify_activity)

    def observe_host(self, host: str, *, reachable: bool) -> None:
        if reachable:
            self.host_observed[host] = self.clock()
            self.unreachable_hosts.discard(host)
        else:
            self.unreachable_hosts.add(host)

    def run_activity(self, run: Run) -> dict:
        observed = run.action_observed_at
        freshness = "unknown"
        host_seen = self.host_observed.get(run.host)
        if run.current_action is not None and observed is not None:
            if run.host in self.unreachable_hosts:
                freshness = "stale"
            elif host_seen is not None:
                freshness = "stale" if (self.clock() - host_seen).total_seconds() > HOST_FRESHNESS_SECONDS else "current"
        return {"action_glyph": run.current_action,
                "action_observed_at": observed.isoformat() if observed is not None else None,
                "action_freshness": freshness}

    def queue_answer(self, item: AttentionItem, decision: "Decision") -> None:
        queue(self.repository, item, decision)

    def grant_permissions(self, item_id: str, scope: str, *, actor: str, activation: str | None = None,
                          complete: Callable[[AttentionItem, str], None] | None = None) -> str:
        """Allow a job step's refused requests ("refused") or all Bash ("bash") for the job; returns what was done."""
        if self.grant is None:
            raise RuntimeError("permission transport is not configured")
        if activation is not None and self.authority is None:
            raise AuthorityRejected('activation authority is not configured')
        def authorize(item: AttentionItem) -> None:
            self.authority().require_triage('grant', item, actor=actor, activation=activation)
            if scope != 'refused':
                raise AuthorityRejected('triage may grant only scope refused')
            violation = refusal_violation(item.refusals, self.authority().triage_mandate(activation))
            if violation is not None:
                raise AuthorityRejected(violation)
        return grant(self.repository, self.grant, item_id, scope, actor,
                     authorize=None if activation is None else authorize, complete=complete)

    def add_triage_step(self, item_id: str, prompt: str, title: str, *, actor: str, activation: str,
                        complete: Callable[[AttentionItem, str], None]) -> str:
        if not prompt.strip() or not title.strip():
            raise ValueError('prompt and title are required')
        if self.step is None:
            raise RuntimeError('step transport is not configured')
        if self.authority is None:
            raise AuthorityRejected('activation authority is not configured')
        with self.repository.transaction() as transaction:
            item = transaction.attention.get(item_id)
        self.authority().require_triage('add_step', item, actor=actor, activation=activation)
        context = item.stream_context
        if context is None or context.owner_type != 'job' or context.source not in (
                'job status failed', 'job status blocked'):
            raise AuthorityRejected('add_step requires a failed or blocked job item')
        if context.source == 'job status blocked' and context.step is None:
            raise AuthorityRejected('blocked job has no observed step to answer')
        details = self.step(StepRequest(context.host, context.owner_id, f'{item.id}:add_step', prompt, title,
                                        context.step if context.blocked_step else None))
        complete(item, details)
        return details

    def retry_triage_job(self, item_id: str, *, actor: str, activation: str) -> str:
        """Legacy unlinked-job retry. The controller durably records its one-shot request before calling."""
        if self.authority is None:
            raise AuthorityRejected('activation authority is not configured')
        with self.repository.transaction() as transaction:
            item = transaction.attention.get(item_id)
        self.authority().require_triage('retry', item, actor=actor, activation=activation)
        if self.step is None:
            raise RuntimeError('step transport is not configured')
        context = item.stream_context
        if context is None or context.owner_type != 'job' or context.source not in (
                'job status failed', 'job status lost'):
            raise AuthorityRejected('retry requires a failed or lost job item')
        return self.step(StepRequest(context.host, context.owner_id, f'{item.id}:retry', retry=True))

    def answer_blocked(self, item_id: str, reply: str, *, actor: str, work_item: str | None = None) -> str:
        """Answer a blocked job step: add a step carrying the reply to the job on its host; returns what was done.

        The reply step serves `work_item` when given, else the work the blocked step served."""
        if self.answer is None:
            raise RuntimeError("answer transport is not configured")
        return answer(self.repository, self.answer, item_id, reply, actor, work_item, self.require_step_work)

    def require_step_work(self, host: str, job: str, work_item: str) -> None:
        """A step added to a job may serve a work item in the job's project; any existing one if it has none."""
        run = self.repository.find(host, job)
        project = None
        if run is not None:
            action = self.repository.get_action(run.action)
            project = action.project if action.work_item is None else self.work.get(action.work_item).project
        require_step_work(self.work, work_item, project)

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

    def activation_run(self, activation: str, idempotency_key: str) -> Run:
        return self.repository.activation_run(activation, idempotency_key)

    def dispatch(self, work_item: str | None, *, activation: str | None = None, **arguments) -> DispatchResult:
        if 'authorization' in arguments:
            raise AuthorityRejected('supply an activation ID')
        if activation is not None:
            if self.authority is None:
                raise AuthorityRejected('activation authority is not configured')
            arguments['authorization'] = self.authority().require('dispatch', work_item,
                actor=arguments['actor'], activation=activation)
            authorization = arguments['authorization']
            if authorization.role == 'triage':
                mandate = self.authority().triage_mandate(activation)
                if (arguments.get('project'), arguments.get('host'), arguments.get('runtime'),
                        arguments.get('payload', {}).get('cwd')) != (
                        authorization.project, mandate.host, mandate.runtime, mandate.cwd):
                    raise AuthorityRejected('triage launch must match pinned project, host, runtime and cwd')
        if self.prepare_dispatch is not None:
            self.prepare_dispatch()
        return dispatch(self.repository, work_item, **arguments)

    def deliver(self, run: Run, call: Callable, push: Callable, *, reconcile: bool = False) -> dict:
        return deliver(self.repository, run, call, push, reconcile=reconcile)

    def start(self, run: Run, call: Callable) -> dict:
        return start(self.repository, run, call)

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
