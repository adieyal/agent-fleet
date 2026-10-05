"""Record decisions locally or hand them to their job controller."""
from __future__ import annotations

import json
import os
import time
from uuid import uuid4

from fleet.projections.decisions import decision_log
from fleet.transport import FleetError, Host


class DecisionCommands:
    def __init__(self, services, workspace, references, transport):
        self.services, self.workspace, self.references, self.transport = services, workspace, references, transport

    def job_run(self, job: str) -> str | None:
        """The run this machine's store holds for the fleet job, or None when it holds none."""
        runs = [run for run in self.services.execution.runs() if run.remote_job_id == job]
        if len(runs) > 1:
            raise FleetError(f"job {job} matches runs on several hosts; give --run")
        return runs[0].id if runs else None

    def hand_to_job(self, job: str, data: dict) -> str:
        """Give the decision to this host's fleetd to hold on the job; the controller records it from the job's stream."""
        decision = {"id": str(uuid4()), "work_item": data["work_item"], "question": data["question"],
                    "answer": data["answer"], "principle": data["principle"], "actor": data["actor"],
                    "context": data["context"], "time": time.time()}
        if not all(decision[name].strip() for name in ("work_item", "question", "answer", "principle", "actor")):
            raise FleetError("work item, question, answer, principle and actor are required")
        held = self.transport.call(Host(os.uname().nodename, None), ["decision", job, "--schema-version", "1"],
                              stdin_text=json.dumps(decision))
        return held["id"]

    def record(self, *, work_item: str, question: str, answer: str, principle: str,
               actor: str, context: str, run: str | None):
        job = os.environ.get("FLEET_JOB_ID")
        run = run if run is not None or job is None else self.job_run(job)
        if run is None and job is not None:
            identity = self.hand_to_job(job, dict(work_item=work_item, question=question, answer=answer,
                                                 principle=principle, actor=actor, context=context))
            return None, identity, job
        work_item = self.references.work(work_item)
        try:
            decision = self.services.decisions.record_guided(work_item, actor=actor, question=question,
                answer=answer, principle=principle, context=context, source_run=run)
        except (ValueError, LookupError) as error:
            raise FleetError(str(error)) from error
        return decision, None, None

    def listing(self, *, epic: str | None, project: str | None):
        work = self.services.work
        try:
            if epic is not None:
                item = work.get(epic)
                if item.kind != 'epic':
                    raise ValueError(f"{item.id} is a {item.kind}, not an epic")
                project, scope = item.project, item.id
            else:
                project, scope = self.workspace().resolve_project(project), None
        except (ValueError, LookupError) as error:
            raise FleetError(str(error)) from error
        return decision_log(work, self.services.decisions, project=project, epic=scope)
