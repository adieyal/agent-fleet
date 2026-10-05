"""Read confirmed pages and resolve their records in a controller snapshot."""
from dataclasses import asdict
from datetime import timedelta
from uuid import UUID, uuid4
import json
import os

from fleet.errors import FleetError
from fleet.services.page_requests import request_token

from fleet.modules.attention import PageAnnotation

from fleet.modules.pages import PagesFacade, PageNotFound, PageInvalid


class PageService:
    def __init__(self, services, pages: PagesFacade):
        self.services, self.pages = services, pages

    def index(self, project):
        project = self.services.workspace.resolve_project(project)
        records = self.services.records.documents(project, prefix='pages/')
        result = []
        for record in records:
            slug = record['path'][6:-3]
            if not record['path'].endswith('.md'):
                continue
            try:
                self.pages.path(slug)
            except ValueError:
                continue
            nodes = self.pages.parse(self.services.records.read(project, record['path']))
            version = self.services.records.document_versions(project, record['path'])[0]
            result.append(dict(slug=slug, title=self.pages.title(nodes, slug), revision=record['revision'],
                               url=f'/pages/{project}/{slug}', author=version.actor, updated=version.time))
        return dict(project=project, pages=sorted(result, key=lambda item: item['slug']),
                    empty_reason=None if result else 'No confirmed pages in this project')

    def write(self, project: str, slug: str, body: str, *, actor: str, key: str | None = None) -> dict:
        job = os.environ.get('FLEET_JOB_ID')
        runs = [] if not job else [run for run in self.services.execution.runs() if run.remote_job_id == job]
        if len(runs) > 1:
            raise FleetError('Job matches several controller runs; page source run is ambiguous')
        if job and not runs:
            raise FleetError('Worker page write refused: put the page in the outbox and report it.')
        path = self.pages.path(slug)
        self.pages.validate(body)
        if not actor.strip():
            raise ValueError('actor is required')
        if key is not None and not key.strip():
            raise ValueError('key is required when supplied')
        project = self.services.workspace.resolve_project(project)
        result = self.services.records.write(project, path, body, actor=actor, key=str(uuid4()) if key is None else key,
                                             source_run=runs[0].id if runs else None)
        if result['state'] != 'confirmed':
            raise FleetError(f'Page write {result["state"]}: {result["error"]}')
        return dict(url=f'/pages/{project}/{slug}', revision=result['revision'])

    def show(self, project: str, slug: str, version: int | None = None) -> dict:
        path = self.pages.path(slug)
        project = self.services.workspace.resolve_project(project)
        record = self.services.records.document(project, path)
        if record is None:
            raise PageNotFound(f'Page not found: {project}/{slug}')
        revision = record['revision']
        if version is not None:
            versions = self.services.records.document_versions(project, path)
            selected = next((item for item in versions if item.number == version), None)
            if selected is None:
                raise PageNotFound(f'Page {slug} has no version {version}')
            revision = selected.revision
        return self.read(project, slug, revision)

    def read(self, project, slug, revision=None):
        path = self.pages.path(slug)
        project = self.services.workspace.resolve_project(project)
        record = self.services.records.document(project, path)
        if record is None:
            raise PageNotFound(f'Page not found: {project}/{slug}')
        if revision is not None and revision not in self.services.records.revisions(project, path):
            raise PageNotFound(f'Unknown confirmed page revision: {revision}')
        revision = revision or record['revision']
        markdown = self.services.records.read(project, path, revision=revision)
        nodes = self.pages.parse(markdown)
        now = self.services.store.clock()
        work = {item.id: item for item in self.services.work.list()}
        attention = {item.id: item for item in self.services.attention.list()}
        actions = {item.id: item for item in self.services.execution.actions()}
        runs = self.services.execution.runs()
        action_projects = {action.id: action.project or (work[action.work_item].project
                           if action.work_item in work else None) for action in actions.values()}
        hosts = {item['name']: item for item in self.services.execution.hosts()}
        resolved = []
        for node in nodes:
            value = asdict(node)
            if node.kind == 'prose':
                value['prose_text'] = self.pages.prose_text(node.text)
            try:
                if node.kind == 'work':
                    item = work.get(node.attributes['id'])
                    if item is None:
                        raise LookupError(f'Unknown work item {node.attributes["id"]}')
                    self.check_project(item.project, project, item.id)
                    value['record'] = {**asdict(item), 'progress': asdict(self.services.work.progress(item.id))}
                elif node.kind == 'attention':
                    item = attention.get(node.attributes['id'])
                    if item is None:
                        raise LookupError(f'Unknown attention item {node.attributes["id"]}')
                    self.check_project(item.project, project, item.id)
                    value['record'] = asdict(item)
                elif node.kind == 'runs':
                    self.check_project(node.attributes['project'], project, node.attributes['project'])
                    days = int(node.attributes['since'][:-1])
                    unknown_start = sum(run.start is None and action_projects[run.action] == project for run in runs)
                    value['excluded_reason'] = (f'{unknown_start} runs excluded: start time not recorded'
                                                if unknown_start else None)
                    matching = [run for run in runs if action_projects[run.action] == project and
                                run.start is not None and now - timedelta(days=days) <= run.start <= now]
                    matching.sort(key=lambda run: run.id)
                    matching.sort(key=lambda run: run.start, reverse=True)
                    value['records'] = [{**asdict(run), 'work_item': actions[run.action].work_item,
                                         'host_reachable': hosts.get(run.host, {}).get('reachable')}
                                        for run in matching]
                    value['empty_reason'] = None if matching else f'No runs for {project} in the last {days} days'
            except LookupError as error:
                value.update(kind='error', error=str(error))
            resolved.append(value)
        address = f'fleet://projects/{project}/pages/{slug}'
        answers = self.services.decisions.list()
        threads = []
        originals = {revision: nodes}
        triage = self.services.triage_repository.get(project)
        for item in attention.values():
            annotation = item.page_annotation
            if annotation is not None and annotation.page == address:
                selectors = annotation.selector if isinstance(annotation.selector, list) else [annotation.selector]
                block = selectors[0].get('value') if selectors[0]['type'] == 'FragmentSelector' else None
                original = None
                if block is not None:
                    if annotation.revision not in originals:
                        originals[annotation.revision] = self.pages.parse(
                            self.services.records.read(project, path, revision=annotation.revision))
                    original = originals[annotation.revision]
                attached = self.pages.attachment(nodes, annotation.selector, original)
                failed = next((node for node in resolved if node['block'] == block and node['kind'] == 'error'), None)
                if block is not None and failed is not None and attached['state'] == 'attached':
                    attached = dict(state='unavailable', block=block,
                                    reason=f'Anchor unavailable: block {block}: {failed["error"]}')
                threads.append(dict(id=item.id, headline=item.headline, owner=item.owner, state=item.state,
                    owner_at=None if item.owner_at is None else item.owner_at.isoformat(),
                    created=item.last_seen, annotation=asdict(annotation), kind=item.kind,
                    replies=[asdict(reply) for reply in item.replies],
                    attachment=attached, agent_status=self.agent_status(item, triage),
                    answers=[asdict(answer) for answer in answers if answer.attention_item == item.id
                             and not self.reply_audit(answer)]))
        threads.sort(key=lambda thread: (str(thread['created']), thread['id']))
        return dict(threads=threads, project=project, slug=slug, title=self.pages.title(nodes, slug), revision=revision,
                    historical=revision != record['revision'], markdown=markdown, nodes=resolved,
                    snapshot_time=now.isoformat(), state_version=self.services.store.latest_sequence(),
                    address=f'fleet://projects/{project}/pages/{slug}')

    @staticmethod
    def reply_audit(answer):
        if not answer.activation:
            return False
        try:
            context = json.loads(answer.context)
        except ValueError:
            return False
        return isinstance(context, dict) and context.get('command') == 'reply_attention'

    def agent_status(self, item, state):
        if item.state == 'resolved':
            return None
        if item.owner == 'user':
            return item.owner_reason if item.owner_actor and item.owner_actor.startswith('triage:') else None
        fallback = state.get('fallback', {}).get(item.id)
        prefix = ('Escalated to fleetd: ' + fallback['reason'] + ' · '
                  if fallback and (fallback['request'] == request_token(item) or item.id in state.get('items', [])) else '')
        if state.get('error'):
            return prefix + 'Agent reply unavailable: ' + state['error']
        if item.id in state.get('items', []) and state.get('run'):
            run = self.services.execution.get_run(state['run'])
            if run.status == 'running':
                return prefix + 'Agent replying…'
            if run.status != 'succeeded':
                return prefix + f'Agent reply run {run.id}: {run.status}'
        try:
            mandate = self.services.records.triage_mandate(item.project)
        except (ValueError, LookupError, OSError) as error:
            return 'Agent reply unavailable: ' + str(error)
        if mandate is None:
            return 'Agent reply unavailable: no confirmed triage mandate'
        if 'reply_attention' not in mandate.decision_authority:
            return 'Agent reply unavailable: mandate does not authorize replies'
        if state.get('handled', {}).get(item.id) == request_token(item):
            return 'Agent replied · thread remains open'
        used = state.get('used', 0) if state.get('day') == self.services.store.clock().date().isoformat() else 0
        if used >= mandate.limits['runs_per_day']:
            reset = (self.services.store.clock() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            return 'Agent reply budget exhausted; resets at ' + reset.isoformat()
        return prefix + 'Agent reply queued'

    @staticmethod
    def check_project(actual, expected, identity):
        if actual != expected:
            raise LookupError(f'Record {identity} belongs to another project')

    def comment(self, project, slug, *, revision, comment_id, headline, body, selector,
                reason, actor, owner='user', parent=None):
        for name, value in dict(revision=revision, comment_id=comment_id, headline=headline,
                                body=body, reason=reason, actor=actor).items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{name} is required')
        if owner not in ('user', 'agent'):
            raise ValueError('owner must be user or agent')
        try:
            if str(UUID(comment_id)) != comment_id:
                raise ValueError('comment_id must be a canonical UUID')
        except ValueError as error:
            raise ValueError('comment_id must be a canonical UUID') from error
        if len(body.encode()) > 8 * 1024 or len(headline.encode()) > 1024 or len(reason.encode()) > 8 * 1024:
            raise ValueError('comment body/reason exceeds 8 KiB or headline exceeds 1 KiB')
        if len(headline.split()) > 12:
            raise ValueError('headline must contain 12 words or fewer')
        if len(json.dumps(selector).encode()) > 16 * 1024:
            raise ValueError('selector exceeds 16 KiB')
        view = self.read(project, slug, revision)
        self.pages.validate_selector(self.pages.parse(view['markdown']), selector)
        entries = selector if isinstance(selector, list) else [selector]
        if entries[0]['type'] == 'FragmentSelector':
            failed = next((node for node in view['nodes'] if node['block'] == entries[0]['value']
                           and node['kind'] == 'error'), None)
            if failed is not None:
                raise PageInvalid(f'Cannot anchor unresolved directive: {failed["error"]}')
        if parent is not None:
            if not isinstance(parent, str):
                raise ValueError('parent must be an attention ID')
            previous = self.services.attention.get(parent)
            if previous.page_annotation is None or previous.page_annotation.page != view['address']:
                raise ValueError('parent comment belongs to another page')
        annotation = PageAnnotation(comment_id, view['address'], revision, body, selector,
                                    actor, owner, reason, headline, parent)
        item = self.services.attention.raise_item(project=view['project'], kind='decision', owner=owner,
            source='page', source_reference=f'page:{view["project"]}:{slug}:{comment_id}',
            headline=headline, context_reference=view['address'], actor=actor,
            owner_reason=reason, page_annotation=annotation)
        return dict(id=item.id, state=item.state)

    def comment_text(self, project, slug, *, body, owner='user', **fields):
        if not isinstance(body, str) or not body.strip():
            raise ValueError('body is required')
        # Presentation-only shorthand; the immutable attention request remains complete.
        headline = ' '.join(body.split()[:12])[:240]
        reason = f'{owner} must respond to this page comment: {headline}'
        return self.comment(project, slug, body=body, owner=owner,
                            headline=headline, reason=reason, **fields)

    def resolve(self, project, slug, *, item_id, actor):
        self.page_item(project, slug, item_id)
        return asdict(self.services.attention.resolve(item_id, actor=actor,
                       details='Resolved from the page margin without an answer.'))

    def page_item(self, project, slug, item_id):
        self.pages.path(slug)
        project = self.services.workspace.resolve_project(project)
        item = self.services.attention.get(item_id)
        if item.page_annotation is None or item.page_annotation.page != f'fleet://projects/{project}/pages/{slug}':
            raise ValueError('answer item does not belong to this page')
        return item

    def reply(self, project, slug, *, item_id, body, actor):
        self.page_item(project, slug, item_id)
        return asdict(self.services.attention.reply(item_id, body, actor=actor))

    def reopen(self, project, slug, *, item_id, actor):
        self.page_item(project, slug, item_id)
        return asdict(self.services.attention.reopen(item_id, actor=actor))

    def answer(self, project, slug, *, item_id, answer, actor):
        self.page_item(project, slug, item_id)
        return asdict(self.services.decisions.answer(item_id, answer, actor=actor))
