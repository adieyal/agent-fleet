"""Accept explicit runtime input transitions without inferring answers from silence.

A person is at an interactive session's prompt, so each of its permission requests is its own
item. A job runs Claude non-interactively: its requests are refused on the spot and the agent
carries on, so they gather into one batch per job step, answered by changing the job's
permissions rather than in words.
"""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from .ports import AttentionRepository
from ..domain import AttentionItem, Question, QuestionOption, Refusal, StreamContext

if TYPE_CHECKING:
    from .observations import HostObservation

Router = Callable[..., tuple[str, str | None]]
JobLink = Callable[[str, str], tuple[str, str | None] | None]



@dataclass(frozen=True)
class InputObservation:
    schema_version: int
    runtime: str
    owner_type: str
    job_id: str | None
    session_id: str
    step_index: int | None
    project: str
    kind: str
    reason: str
    source_event: str
    source_event_id: str
    observed_at: float
    context_reference: str
    request: dict | None = None  # tool, description, detail, rules, questions; absent from older fleetd
    cwd: str | None = None       # the session's working directory; absent from older fleetd

    def questions(self) -> tuple[Question, ...]:
        return tuple(Question(question.get("header", ""), question.get("question", ""),
                              tuple(QuestionOption(option.get("label", ""), option.get("description", ""))
                                    for option in question.get("options", [])),
                              question.get("multi_select") is True)
                     for question in (self.request or {}).get("questions") or [])

    def question(self) -> tuple[str, str]:
        """The headline and context a person reads to answer the request."""
        questions = self.questions()
        if questions:
            return question_headline(questions[0]), question_context(questions)
        if not self.request:
            return "Claude needs permission", self.context_reference
        parts = [self.request.get("description", ""), self.request.get("detail", "")]
        context = "\n\n".join(part for part in parts if part) or self.context_reference
        return f"Claude asks to use {self.request.get('tool') or 'a tool'}", context

    def refusal(self) -> Refusal:
        request = self.request or {}
        rules = request.get("rules")
        return Refusal(self.source_event_id, request.get("tool") or "a tool", request.get("description", ""),
                       request.get("detail") or self.context_reference,
                       None if rules is None else tuple(rules), self.observed_at,
                       tuple(request.get("denied_by") or ()))


def question_headline(question: Question) -> str:
    """The header and the start of the question, at most 12 words."""
    words = f"{question.header}:".split() if question.header.strip() else []
    words += question.question.split()
    if not words:
        return "Claude asks you a question"
    return " ".join(words) if len(words) <= 12 else " ".join(words[:12]) + "…"


def question_context(questions: tuple[Question, ...]) -> str:
    return "\n\n".join(
        "\n".join([f"{question.header}: {question.question}" if question.header else question.question]
                  + [f"- {option.label}" + (f": {option.description}" if option.description else "")
                     for option in question.options])
        for question in questions)


def batch_headline(project: str, step: int, refusals: tuple[Refusal, ...]) -> str:
    """At most 12 words, e.g. 'restoke step 2: 7 commands refused (Bash ×6, Read ×1)'."""
    prefix = project.split()[:3] + ["step", f"{step + 1}:"]
    count = [str(len(refusals)), "command" if len(refusals) == 1 else "commands", "refused"]
    tools = [f"{tool} ×{number}" for tool, number in Counter(refusal.tool for refusal in refusals).most_common()]
    room = 12 - len(prefix) - len(count)
    if len(tools) * 2 > room:
        keep = (room - 2) // 2
        tools = tools[:keep] + [f"+{len(tools) - keep} more"]
    return " ".join(prefix + count) + f" ({', '.join(tools)})"


def batch_context(refusals: tuple[Refusal, ...]) -> str:
    return "\n".join(f"{refusal.tool}: {refusal.detail}" + (f" — {refusal.description}" if refusal.description else "")
                     for refusal in refusals)


def hook_covers_question(items: list[AttentionItem], host: str, session: str, since: float | None) -> bool:
    """Prefer the terminal hook's question; a cleared hook also covers its older transcript activity."""
    return any(item.source == f"runtime-input:{host}" and item.subject == f"session:{host}:{session}"
               and item.stream_context is not None and item.stream_context.source == "Claude question"
               and (item.state != "resolved" or (since is not None and since <= item.last_seen.timestamp()))
               for item in items)


def ingest_input(repository: AttentionRepository, host: str, observation: InputObservation,
                 project_id: str | None, *, router: Router | None = None,
                 run: str | None = None, work_item: str | None = None) -> None:
    if observation.schema_version != 1:
        raise ValueError("unsupported input observation schema version")
    if observation.runtime != "claude":
        raise ValueError("unsupported input observation runtime")
    if observation.owner_type not in ("job", "session"):
        raise ValueError("invalid input observation owner type")
    if (observation.kind, observation.source_event) not in (
            ("input_requested", "PermissionRequest"), ("input_requested", "PreToolUse"),
            ("input_cleared", "PostToolUse")):
        raise ValueError("unsupported input observation transition")
    owner_id = observation.job_id if observation.owner_type == "job" else observation.session_id
    if not owner_id or not observation.source_event_id:
        raise ValueError("input observation requires owner and occurrence identity")
    if observation.owner_type == "job":
        if observation.step_index is None:
            raise ValueError("a job's input observation requires its step")
        ingest_refusal(repository, host, observation, project_id, router=router, run=run, work_item=work_item)
        return
    owner = f"{observation.owner_type}:{host}:{owner_id}"
    source = f"runtime-input:{host}"
    reference = f"{owner}:{observation.source_event_id}"
    seen = datetime.fromtimestamp(observation.observed_at, timezone.utc)
    with repository.transaction() as transaction:
        previous = transaction.find(source, reference)
        if previous is not None and (previous.state == "resolved" or previous.last_seen > seen):
            return
        if observation.reason == "question":
            for duplicate in transaction.list():
                context = duplicate.stream_context
                if (duplicate.source == f"stream:{host}" and duplicate.subject == owner
                        and duplicate.state != "resolved" and context is not None
                        and context.source == "session tool AskUserQuestion"
                        and (observation.kind == "input_requested" or context.since is None
                             or context.since <= observation.observed_at)):
                    transaction.save(duplicate.transition("resolved", seen,
                        details=f"superseded by session question {reference}"), duplicate.state, "runtime-hook")
        headline, detail = observation.question()
        questions = observation.questions()
        if previous is not None and observation.kind == "input_requested":
            if (previous.headline, previous.context_reference, previous.questions) == (headline, detail, questions):
                return
            # Items recorded before fleetd sent the request text pick it up on replay.
            context = previous.stream_context and replace(previous.stream_context, summary=headline)
            transaction.save(replace(previous, headline=headline, context_reference=detail, stream_context=context,
                                     questions=questions), previous.state, "runtime-hook")
            return
        context = StreamContext(host, observation.owner_type, owner_id, observation.project, project_id,
                                "Claude question" if observation.reason == "question" else "Claude permission request",
                                headline, observation.observed_at, cwd=observation.cwd)
        project = project_id if project_id is not None else observation.project
        routed_owner, owner_reason = (('user', None) if previous is not None or router is None else
                                      router(project, 'decision', context))
        item = previous if previous is not None else AttentionItem(
            id=str(uuid4()), project=project,
            work_item=None, run=None, kind="decision", owner=routed_owner,
            owner_reason=owner_reason, owner_at=seen, owner_actor="runtime-hook", subject=owner, source=source,
            source_reference=reference, headline=headline,
            context_reference=detail, state="open", snooze_until=None,
            resolution_details=None, last_seen=seen, stream_context=context, questions=questions)
        if observation.kind == "input_cleared":
            item = replace(item.transition("resolved", seen, details="answered in session"), last_seen=seen)
        transaction.save(item, previous.state if previous is not None else None, "runtime-hook")


def ingest_refusal(repository: AttentionRepository, host: str, observation: InputObservation,
                   project_id: str | None, *, router: Router | None = None,
                   run: str | None = None, work_item: str | None = None) -> None:
    """Gather a job's refused request into its step's batch, folding in any per-request item left from before."""
    step = observation.step_index
    assert step is not None and observation.job_id is not None
    owner = f"job:{host}:{observation.job_id}"
    source = f"runtime-input:{host}"
    seen = datetime.fromtimestamp(observation.observed_at, timezone.utc)
    with repository.transaction() as transaction:
        single = transaction.find(source, f"{owner}:{observation.source_event_id}")
        if single is not None and single.state != "resolved":
            transaction.save(single.transition("resolved", seen, details=f"folded into step {step + 1}'s refusals"),
                             single.state, "runtime-hook")
        batch = transaction.find(source, f"{owner}:step:{step}")
        if batch is not None and run is not None and (batch.run, batch.work_item) != (run, work_item):
            linked = replace(batch, run=run, work_item=work_item)
            transaction.save(linked, batch.state, "runtime-hook")
            batch = linked
        if batch is not None and batch.state == "resolved":
            return
        refusals = batch.refusals if batch is not None else ()
        known = next((refusal for refusal in refusals if refusal.occurrence == observation.source_event_id), None)
        if observation.kind == "input_cleared":
            # The request ran after all (PostToolUse), so it was never a refusal.
            refusals = tuple(refusal for refusal in refusals if refusal is not known)
        elif known is None:
            refusals = refusals + (observation.refusal(),)
        else:
            # A replay may carry request text or rules an older record lacked.
            refusals = tuple(observation.refusal() if refusal is known else refusal for refusal in refusals)
        if batch is not None and refusals == batch.refusals:
            return
        if batch is None and not refusals:
            return
        if not refusals:
            transaction.save(batch.transition("resolved", seen, details="the refused requests ran after all"),
                             batch.state, "runtime-hook")
            return
        headline = batch_headline(observation.project, step, refusals)
        context = StreamContext(host, "job", observation.job_id, observation.project, project_id,
                                "Claude permission refusals", headline,
                                min(refusal.observed_at for refusal in refusals), step=step)
        last_seen = datetime.fromtimestamp(max(refusal.observed_at for refusal in refusals), timezone.utc)
        if batch is None:
            project = project_id if project_id is not None else observation.project
            routed_owner, owner_reason = (('user', None) if router is None else
                                          router(project, 'decision', context, refusals=refusals))
            batch = AttentionItem(
                id=str(uuid4()), project=project,
                work_item=work_item, run=run, kind="decision", owner=routed_owner,
                owner_reason=owner_reason, owner_at=last_seen, owner_actor="runtime-hook", subject=owner, source=source,
                source_reference=f"{owner}:step:{step}", headline=headline,
                context_reference=batch_context(refusals), state="open", snooze_until=None,
                resolution_details=None, last_seen=last_seen, stream_context=context, refusals=refusals)
            transaction.save(batch, None, "runtime-hook")
            return
        transaction.save(replace(batch, headline=headline, context_reference=batch_context(refusals),
                                 stream_context=context, refusals=refusals, last_seen=last_seen),
                         batch.state, "runtime-hook")


def close_refusals(repository: AttentionRepository, host: "HostObservation", *, complete: bool,
                   now: datetime, job_link: JobLink | None = None) -> bool:
    """Resolve refusal batches the job has moved past; nothing is left to answer there.

    A batch stays answerable after its own step ends, since that is when a refused step is
    usually noticed and allowing it queues a continuation. It closes once a later step of the
    job has started, once the job finishes successfully or is cancelled/lost, or once it is gone.
    `complete` means the host has reported every job it still keeps since reconnecting, so a
    batch whose job is absent belongs to a job that finished long ago or was removed, and a
    per-request job item that was not folded on replay is superseded.
    """
    if not host["ok"]:
        return False
    source = f"runtime-input:{host['name']}"
    jobs = {job["id"]: job for job in host["jobs"]}
    closing = []
    linking = []
    for item in repository.list():
        context = item.stream_context
        if item.source != source or context is None or context.owner_type != "job":
            continue
        link = job_link(host["name"], context.owner_id) if job_link else None
        if link is not None and (item.run, item.work_item) != link:
            linking.append((item.id, link))
        if item.state == "resolved":
            continue
        job = jobs.get(context.owner_id)
        if context.step is None:
            details = "superseded: job refusals are gathered per step" if complete else None
        elif job is None:
            details = "refused; job finished or removed" if complete else None
        elif job["status"] in ("done", "cancelled", "lost"):
            details = "refused; job finished"
        else:
            later = [step["index"] for step in job["steps"] if step["index"] > context.step
                     and (step["status"] == "running" or step.get("started_at") is not None)]
            details = f"refused; the job went on to step {max(later) + 1}" if later else None
        if details is not None:
            closing.append((item.id, details))
    if not closing and not linking:
        return False
    with repository.transaction() as transaction:
        for item_id, (run, work_item) in linking:
            item = transaction.get(item_id)
            transaction.save(replace(item, run=run, work_item=work_item), item.state, "runtime-hook")
        for item_id, details in closing:
            item = transaction.get(item_id)
            if item.state != "resolved":
                transaction.save(item.transition("resolved", now, details=details), item.state, "runtime-hook")
    return True
