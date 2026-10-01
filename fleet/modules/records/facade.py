"""Authored documents, summaries and mandates."""

import json
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

from .application import Authoring
from .domain import CONSTITUTION, GUIDANCE_FILES, Guidance, GuidanceConflict, Mandate, Version, charter_path, in_force
from .domain import TRIAGE_PATH, TriageMandate


class RecordsFacade:
    def __init__(self, repository, writer, workspace, work, home: Path | None = None):
        """home: where fleet keeps the management repositories it creates; None leaves creation to registration."""
        self.repository, self.writer, self.workspace = repository, writer, workspace
        self._work = work
        self.home = home
        self.authoring = Authoring(repository, writer, workspace)

    @property
    def work(self):
        return self._work()

    def register(self, project: str, path, *, actor: str) -> int:
        """Register the repository and move the project's store summaries into it; returns how many moved."""
        root = self.writer.root(path)
        self.workspace.register_management_repository(project, root, actor=actor)
        summaries = self.work.legacy_summaries(project)
        for summary in summaries:
            result = self.write(project, f'summaries/{summary.id}.json',
                                json.dumps(asdict(summary), default=str), key=f'cutover:{summary.id}', actor=actor)
            if result['state'] != 'confirmed':
                raise ValueError(result['error'])
            self.work.retire_summary(summary.id, actor=actor)
        return len(summaries)

    def write(self, project: str, path: str, body: str, **fields) -> dict:
        self.provide(project, actor=fields['actor'])
        return self.authoring.write(project, path, body, **fields)

    def prepare(self, project: str, path: str, body: str, **fields) -> dict:
        self.provide(project, actor=fields['actor'])
        return self.authoring.prepare(project, path, body, **fields)

    def provide(self, project: str, *, actor: str) -> None:
        """A project's first record creates its management repository under fleet's home for them, so no author has
        to choose or register one; a repository registered earlier, anywhere, is kept."""
        if self.registered(project) or self.home is None:
            return
        root = self.home / project
        root.mkdir(parents=True, exist_ok=True)
        if not (root / '.git').exists():
            self.writer.git(root, 'init', '-q')
        self.register(project, root, actor=actor)

    def publish(self, intent: dict, body: str) -> dict:
        return self.authoring.publish(intent, body)

    def reconcile(self) -> None:
        self.authoring.reconcile()

    def intents(self) -> list[dict]:
        return self.repository.list()

    def read(self, project: str, path: str, *, revision: str | None = None) -> str | None:
        record = self.repository.current(project, path)
        if record is None:
            return None
        return self.writer.read(self.workspace.management_repository(project), path, revision or record['revision'])

    def write_summary(self, summary, project: str, *, actor: str, source_run: str | None = None) -> None:
        result = self.write(project, f'summaries/{summary.id}.json', json.dumps(asdict(summary), default=str),
                            key=str(uuid4()), actor=actor, source_run=source_run)
        if result['state'] != 'confirmed':
            raise ValueError(result['error'])

    def write_mandate(self, project: str, path: str, body: str, **fields) -> dict:
        (TriageMandate if path == TRIAGE_PATH else Mandate).parse(body)
        return self.write(project, path, body, **fields)

    def triage_mandate(self, project: str) -> TriageMandate | None:
        body = self.read(project, TRIAGE_PATH)
        return None if body is None else TriageMandate.parse(body)

    def mandate(self, project: str, path: str) -> Mandate:
        body = self.read(project, path)
        if body is None:
            raise LookupError('mandate is not recorded')
        return (TriageMandate if path == TRIAGE_PATH else Mandate).parse(body)

    def registered(self, project: str) -> bool:
        """Whether the project has a management repository to hold its records."""
        try:
            self.workspace.management_repository(project)
        except ValueError:
            return False
        return True

    def guidance(self, project: str, epic: str | None = None, *, number: int | None = None) -> Guidance | None:
        """The project's constitution, or the epic's charter with the constitution versions it inherits;
        the current version unless number names an older one. None when nothing is recorded, including before the
        project's first record has created its management repository."""
        path = self.guidance_path(project, epic)
        record = self.repository.current(project, path) if self.registered(project) else None
        if record is None:
            if number is not None:
                raise LookupError(f'{path} has no version {number}')
            return None
        root = self.workspace.management_repository(project)
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
                       source_run: str | None = None, base: int | None = None) -> Guidance:
        """A new version; base, when given, is the version number the editor started from (0 for none), and a
        newer current version refuses the write."""
        if not body.strip():
            raise ValueError('guidance is empty')
        path = self.guidance_path(project, epic)
        self.provide(project, actor=actor)
        current = self.guidance(project, epic)
        number = 0 if current is None else current.version.number
        if base is not None and base != number:
            raise GuidanceConflict(f'{path} changed since version {base}: it is now version {number}')
        if current is not None and current.body == body:
            raise ValueError(f'unchanged from version {current.version.number}')
        result = self.write(project, path, body, key=str(uuid4()), actor=actor, source_run=source_run)
        if result['state'] != 'confirmed':
            raise ValueError(result['error'])
        return self.guidance(project, epic)

    def promote(self, project: str, epic: str, text: str, *, marker: str, actor: str) -> Guidance:
        """Add text to the charter's decisions in force as a new version; marker identifies what was promoted,
        so promoting it twice is refused."""
        current = self.guidance(project, epic)
        if current is None:
            raise LookupError(f'no charter recorded for epic {epic}; write one before promoting decisions')
        if marker in current.body:
            raise ValueError(f'{marker} is already in the charter')
        return self.write_guidance(project, in_force(current.body, text), epic=epic, actor=actor,
                                   base=current.version.number)

    def guidance_history(self, project: str, epic: str | None = None) -> list[Version]:
        """Versions of the constitution or charter, newest first."""
        path = self.guidance_path(project, epic)
        record = self.repository.current(project, path) if self.registered(project) else None
        if record is None:
            return []
        return self.versions(self.workspace.management_repository(project), path, record['revision'])

    def dispatch_guidance(self, work_item: str) -> dict | None:
        """Current constitution and nearest-epic charter versions for a job on work_item; None when neither is
        recorded, so an unguided project dispatches as before."""
        project = self.work.get(work_item).project
        epic = self.work.nearest_epic(work_item)
        paths = dict(constitution=CONSTITUTION, charter=None if epic is None else charter_path(epic.id))
        records = {name: None if path is None else self.repository.current(project, path)
                   for name, path in paths.items()}
        if not any(records.values()):
            return None
        root = self.workspace.management_repository(project)
        guidance = dict(project=project, epic=None if epic is None else epic.id)
        for name, record in records.items():
            version = None if record is None else self.versions(root, paths[name], record['revision'])[0]
            guidance[name] = None if version is None else dict(path=paths[name], revision=version.revision,
                                                                 version=version.number)
        return guidance

    def write_guidance_files(self, guidance: dict, directory) -> list[str]:
        """Write the pinned versions as CONSTITUTION.md and CHARTER.md into directory."""
        root = self.workspace.management_repository(guidance['project'])
        written = []
        for name, file in GUIDANCE_FILES.items():
            if guidance[name] is not None:
                target = Path(directory) / file
                target.write_text(self.writer.read(root, guidance[name]['path'], guidance[name]['revision']))
                written.append(str(target))
        return written

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
        return revision, (TriageMandate if path == TRIAGE_PATH else Mandate).parse(body)
