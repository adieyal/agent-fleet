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


def relevant(work: WorkFacade, identity: str | None, decision: "Decision") -> bool:
    while identity is not None:
        if identity in decision.affected_work_items:
            return True
        identity = work.get(identity).parent
    return False


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
        for decision in decisions:
            key = f'context-decision:{run.id}:{decision.id}'
            earlier = decision.id in baseline if baseline is not None else decision.time <= boundary
            if key in existing or earlier or not relevant(execution.work, action.work_item, decision):
                continue
            with execution.repository.transaction() as transaction:
                if any(d.key == key for d in transaction.deliveries()):
                    continue
                transaction.save_delivery(Delivery(key, decision.id, run.id,
                    json.dumps(asdict(decision), default=lambda value: value.isoformat())), decision.actor)
            existing.add(key)
