"""Controller reconciliation of decisions into durable worker inbox deliveries."""

import json
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING

from fleet.modules.work import WorkFacade

if TYPE_CHECKING:
    from fleet.modules.decisions import Decision
    from ..facade import ExecutionFacade

from ..domain import Delivery


def lineage(work: WorkFacade, identity: str | None) -> set[str]:
    """The work item and its ancestors."""
    items = set()
    while identity is not None:
        items.add(identity)
        identity = work.get(identity).parent
    return items


def relevant(work: WorkFacade, identity: str | None, decision: "Decision") -> bool:
    return not lineage(work, identity).isdisjoint(decision.affected_work_items)


def reconcile(execution: "ExecutionFacade", decisions: list["Decision"]) -> None:
    existing = {delivery.key for delivery in execution.deliveries()}
    for run in execution.repository.decision_runs():
        if run.kind != 'job' or run.status not in ('running', 'unknown outcome'):
            continue
        action = execution.get_action(run.action)
        baseline = (action.payload or {}).get('decisions_dispatch_ids')
        boundary = (action.payload or {}).get('decisions_dispatch_at')
        boundary = datetime.fromisoformat(boundary) if boundary else run.start
        if (baseline is None and boundary is None) or action.work_item is None:
            continue
        # One ancestry walk per run; walking it per decision made each streamed decision cost O(decisions) reads.
        items = lineage(execution.work, action.work_item)
        for decision in decisions:
            key = f'context-decision:{run.id}:{decision.id}'
            earlier = decision.id in baseline if baseline is not None else decision.time <= boundary
            if key in existing or earlier or items.isdisjoint(decision.affected_work_items):
                continue
            with execution.repository.transaction() as transaction:
                if any(d.key == key for d in transaction.deliveries()):
                    continue
                transaction.save_delivery(Delivery(key, decision.id, run.id,
                    json.dumps(asdict(decision), default=lambda value: value.isoformat())), decision.actor)
            existing.add(key)
