"""Persist input intent before transport, and reconcile worker acknowledgements."""

from dataclasses import replace
from typing import TYPE_CHECKING

from ..domain import Delivery
from .ports import ExecutionRepository, InputSender
from fleet.modules.attention import AttentionItem
from fleet.modules.work import WorkFacade

if TYPE_CHECKING:
    from fleet.modules.decisions import Decision


def queue(repository: ExecutionRepository, item: AttentionItem, decision: "Decision") -> None:
    run = next((run for run in repository.runs() if run.id == item.run), None)
    if run is None and item.stream_context is not None and item.stream_context.owner_type == "job":
        context = item.stream_context
        run = repository.find(context.host, context.owner_id)
    if run is None:
        return
    with repository.transaction() as transaction:
        transaction.save_delivery(Delivery(decision.id, decision.id, run.id, decision.answer), decision.actor)


def retry(repository: ExecutionRepository, work: WorkFacade, send: InputSender,
          host: str | None, decision: str | None, *, context_only: bool = False) -> None:
    for delivery in repository.pending_deliveries():
        if context_only and not delivery.key.startswith("context-decision:"):
            continue
        run = repository.get_run(delivery.run)
        if delivery.status == "applied" or (host is not None and run.host != host):
            continue
        if decision is not None and delivery.decision != decision:
            continue
        result = send(run, delivery)
        if result.status == "busy":
            continue
        with repository.transaction() as transaction:
            current = transaction.get_delivery(delivery.key)
            if current is None:
                continue
            if current.status == "applied":
                continue
            updated = (replace(current, status="applied", error=None) if result.status == "applied" else
                       replace(current, failures=min(3, current.failures + 1), error=result.error))
            if updated != current:
                transaction.save_delivery(updated, "delivery")
            if current.failures < 3 and updated.failures == 3:
                action = transaction.get_action(run.action)
                item = work.get(action.work_item)
                transaction.attention.raise_item(project=item.project, work_item=item.id, run=run.id,
                    kind="alert", owner="user", subject=f"run:{run.id}", source="input-delivery",
                    source_reference=delivery.key, headline="Decision delivery keeps failing" if delivery.key.startswith("context-decision:") else "Answer delivery keeps failing",
                    context_reference=f"decision:{delivery.decision}", actor="delivery")
            if result.status == "applied":
                for item in transaction.attention.list(source="input-delivery", source_reference=delivery.key):
                    if item.source == "input-delivery" and item.source_reference == delivery.key and item.state != "resolved":
                        transaction.attention.resolve(item.id, details="Answer delivered", actor="delivery")
