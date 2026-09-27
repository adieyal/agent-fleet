"""Authored documents, summaries and mandates."""

import json
from dataclasses import asdict
from uuid import uuid4

from .application import Authoring
from .domain import Mandate


class RecordsFacade:
    def __init__(self, repository, writer, workspace, work):
        self.repository, self.writer, self.workspace = repository, writer, workspace
        self._work = work
        self.authoring = Authoring(repository, writer, workspace)

    @property
    def work(self):
        return self._work()

    def register(self, project: str, path, *, actor: str) -> None:
        root = self.writer.root(path)
        self.workspace.register_management_repository(project, root, actor=actor)
        for summary in self.work.legacy_summaries(project):
            result = self.write(project, f'summaries/{summary.id}.json',
                                json.dumps(asdict(summary), default=str), key=f'cutover:{summary.id}', actor=actor)
            if result['state'] != 'confirmed':
                raise ValueError(result['error'])
            self.work.retire_summary(summary.id, actor=actor)

    def write(self, project: str, path: str, body: str, **fields) -> dict:
        return self.authoring.write(project, path, body, **fields)

    def prepare(self, project: str, path: str, body: str, **fields) -> dict:
        return self.authoring.prepare(project, path, body, **fields)

    def publish(self, intent: dict, body: str) -> dict:
        return self.authoring.publish(intent, body)

    def reconcile(self) -> None:
        self.authoring.reconcile()

    def intents(self) -> list[dict]:
        return self.repository.list()

    def read(self, project: str, path: str) -> str | None:
        record = self.repository.current(project, path)
        if record is None:
            return None
        return self.writer.read(self.workspace.management_repository(project), path, record['revision'])

    def write_summary(self, summary, project: str, *, actor: str, source_run: str | None = None) -> None:
        result = self.write(project, f'summaries/{summary.id}.json', json.dumps(asdict(summary), default=str),
                            key=str(uuid4()), actor=actor, source_run=source_run)
        if result['state'] != 'confirmed':
            raise ValueError(result['error'])

    def write_mandate(self, project: str, path: str, body: str, **fields) -> dict:
        Mandate.parse(body)
        return self.write(project, path, body, **fields)

    def mandate(self, project: str, path: str) -> Mandate:
        body = self.read(project, path)
        if body is None:
            raise LookupError('mandate is not recorded')
        return Mandate.parse(body)

    def mandate_version(self, project: str, path: str, *, revision: str | None = None) -> tuple[str, Mandate]:
        if revision is None:
            record = self.repository.current(project, path)
            if record is None:
                raise LookupError('mandate is not recorded')
            revision = record['revision']
        body = self.writer.read(self.workspace.management_repository(project), path, revision)
        return revision, Mandate.parse(body)
