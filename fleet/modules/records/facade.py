"""Authored documents, summaries and mandates."""

import json
from dataclasses import asdict, replace
from uuid import uuid4

from .application import Authoring
from .domain import CONSTITUTION, Guidance, Mandate, Version, charter_path


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

    def guidance(self, project: str, epic: str | None = None, *, number: int | None = None) -> Guidance | None:
        """The project's constitution, or the epic's charter with the constitution versions it inherits;
        the current version unless number names an older one."""
        path = self.guidance_path(project, epic)
        root = self.workspace.management_repository(project)
        record = self.repository.current(project, path)
        if record is None:
            if number is not None:
                raise LookupError(f'{path} has no version {number}')
            return None
        versions = self.versions(root, path, record['revision'])
        version = versions[0] if number is None else next((v for v in versions if v.number == number), None)
        if version is None:
            raise LookupError(f'{path} has no version {number}; versions are 1 to {versions[0].number}')
        guidance = Guidance(path, self.writer.read(root, path, version.revision), version)
        if epic is None:
            return guidance
        constitution = self.repository.current(project, CONSTITUTION)
        if constitution is None:
            return guidance
        inherits = self.versions(root, CONSTITUTION, version.revision)
        return replace(guidance, inherits=inherits[0] if inherits else None,
                       constitution=self.versions(root, CONSTITUTION, constitution['revision'])[0])

    def write_guidance(self, project: str, body: str, *, epic: str | None = None, actor: str,
                       source_run: str | None = None) -> Guidance:
        if not body.strip():
            raise ValueError('guidance is empty')
        path = self.guidance_path(project, epic)
        current = self.guidance(project, epic)
        if current is not None and current.body == body:
            raise ValueError(f'unchanged from version {current.version.number}')
        result = self.write(project, path, body, key=str(uuid4()), actor=actor, source_run=source_run)
        if result['state'] != 'confirmed':
            raise ValueError(result['error'])
        return self.guidance(project, epic)

    def guidance_history(self, project: str, epic: str | None = None) -> list[Version]:
        """Versions of the constitution or charter, newest first."""
        path = self.guidance_path(project, epic)
        root = self.workspace.management_repository(project)
        record = self.repository.current(project, path)
        if record is None:
            return []
        return self.versions(root, path, record['revision'])

    def guidance_path(self, project: str, epic: str | None) -> str:
        if epic is None:
            return CONSTITUTION
        item = self.work.get(epic)
        if item.kind != 'epic':
            raise ValueError(f'{epic} is a {item.kind}; charters belong to epics')
        if item.project != project:
            raise ValueError(f'epic {epic} belongs to {item.project}, not {project}')
        return charter_path(epic)

    def versions(self, root: str, path: str, revision: str) -> list[Version]:
        log = self.writer.log(root, path, revision)
        return [Version(number=len(log) - index, **entry) for index, entry in enumerate(log)]

    def mandate_version(self, project: str, path: str, *, revision: str | None = None) -> tuple[str, Mandate]:
        if revision is None:
            record = self.repository.current(project, path)
            if record is None:
                raise LookupError('mandate is not recorded')
            revision = record['revision']
        body = self.writer.read(self.workspace.management_repository(project), path, revision)
        return revision, Mandate.parse(body)
