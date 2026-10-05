"""Read-model enrichment for controller state."""
import contextlib
from typing import Any
from fleet.errors import FleetError



class LibraryProjection:
    def library_projects(self) -> list[dict[str, Any]]:
        """Every project with a document store: its jobs newest first, each saying whether it is still on its
        host, and its working documents. Stored documents stay readable whatever the host's state."""
        registry, hosts = self.registry, self.job_hosts()
        projects = []
        for project_id in self.documents.projects():
            jobs = self.stored_jobs(project_id, hosts)
            project = registry.projects.get(project_id)
            projects.append({"id": project_id, "name": project.name if project else None,
                             "jobs": jobs, "working": self.documents.working(project_id)})
        return projects

    def library_overview(self, library: Any) -> list[dict[str, Any]]:
        """Each project's overview (see fleet.projections.overview): every project with a library root or a document store."""
        overview = self.__dict__.setdefault("overview", self.container.overview())
        registry, hosts = self.registry, self.job_hosts()
        attention = self.container.attention_items(attention=self.attention, hosts=[{"name": name, "ok": ok} for name, (ok, _) in hosts.items()])
        documents = library.list()
        projects: dict[str, dict[str, Any]] = {}
        for key in sorted(library.roots):
            project_id = self.library_project_id(key)
            projects.setdefault(project_id or "library:" + key, {"project_id": project_id, "library": key})
        for project_id in self.documents.projects():
            projects.setdefault(project_id, {"project_id": project_id, "library": None})
        result = []
        for entry in projects.values():
            project_id, key = entry["project_id"], entry["library"]
            project = registry.projects.get(project_id) if project_id else None
            jobs = self.stored_jobs(project_id, hosts) if project_id else []
            result.append(overview.build(
                name=project.name if project else key or project_id, project_id=project_id, library=key,
                root=library.root(key) if key else None,
                documents=[document for document in documents if document["project"] == key] if key else [],
                jobs=jobs, attention=attention, now=self.clock(),
                read_job=lambda job_key, document_id, project_id=project_id: self.documents.text(project_id, job_key, document_id)))
        return result

    def stored_jobs(self, project_id: str, hosts: dict[str, tuple[bool, set[str]]]) -> list[dict[str, Any]]:
        jobs = self.documents.jobs(project_id)
        for job in jobs:
            reachable, listed = hosts.get(job["host"], (False, set()))
            job["availability"] = ("on host" if reachable and job["id"] in listed
                                   else "gone from host" if reachable else "host offline")
        return jobs

    def library_project_id(self, key: str) -> str | None:
        """The project a `fleet library add` key names: its ID, its name, or a label linked to it on one project."""
        with contextlib.suppress(FleetError):
            return self.registry.resolve(key)
        owners = {project.id for project in self.registry.projects.values()
                  if any(link.label == key for link in project.links)}
        return owners.pop() if len(owners) == 1 else None

    def library_document(self, library):
        documents = library.list()
        for document in documents:
            document['project_id'] = self.library_project_id(document['project'])
        return {'documents': documents, 'projects': self.library_projects()}

    def history_runs(self, filters):
        result = self.container.history_runs(**filters)
        jobs_cache = {}
        for run in result['runs']:
            run['document_count'] = len(self.documents.run_documents(run, jobs_cache=jobs_cache))
        return result

    def history_detail(self, identity):
        result = self.container.run_detail(identity=identity)
        result['kept_documents'] = self.documents.run_documents(result['run'])
        return result

    def stored_document(self, section, project, document_id, job=None):
        return (self.documents.read(project, job, document_id) if section == 'job'
                else self.documents.read_working(project, document_id))
