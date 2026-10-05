"""Read confirmed pages and resolve their records in a controller snapshot."""
from dataclasses import asdict
from datetime import timedelta

from fleet.modules.pages import PagesFacade, PageNotFound


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
            result.append(dict(slug=slug, title=self.pages.title(nodes, slug), revision=record['revision'],
                               url=f'/pages/{project}/{slug}'))
        return dict(project=project, pages=sorted(result, key=lambda item: item['slug']),
                    empty_reason=None if result else 'No confirmed pages in this project')

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
        return dict(project=project, slug=slug, title=self.pages.title(nodes, slug), revision=revision,
                    historical=revision != record['revision'], markdown=markdown, nodes=resolved,
                    snapshot_time=now.isoformat(), state_version=self.services.store.latest_sequence(),
                    address=f'fleet://projects/{project}/pages/{slug}')

    @staticmethod
    def check_project(actual, expected, identity):
        if actual != expected:
            raise LookupError(f'Record {identity} belongs to another project')
