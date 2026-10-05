"""Confirmed triage policy versioning and status queries."""
from __future__ import annotations

import json
from dataclasses import asdict
from uuid import uuid4

from fleet.modules.records import TRIAGE_PATH, TriageMandate


class TriagePolicy:
    def __init__(self, services, workspace, scheduler):
        self.services, self.workspace, self.scheduler = services, workspace, scheduler

    def show(self, reference: str):
        project = self.services.workspace.resolve_project(reference)
        return project, self.services.records.triage_policy(project)

    def set(self, reference: str, body: str, *, actor: str):
        project, before = self.show(reference)
        records = self.services.records
        policy = asdict(TriageMandate.parse(body))
        if policy['criteria_it_may_judge']:
            raise ValueError('triage may not judge work criteria')
        previous = None if before is None else before['policy']
        if previous == policy:
            return project, before, before, True
        result = records.write_mandate(project, TRIAGE_PATH, json.dumps(policy, indent=2) + '\n', key=str(uuid4()), actor=actor)
        if result['state'] != 'confirmed':
            raise ValueError(result['error'])
        return project, before, records.triage_policy(project), False

    def status(self, reference: str):
        project = self.workspace().resolve_project(reference)
        return project, self.scheduler().status(project)
