"""Read-model enrichment for controller state."""
import json
import time
from dataclasses import asdict
from typing import Any
from fleet.modules.workspace import Registry
from fleet.projections.workspace import annotate, resolve, registry_config
from fleet.projections.attention import attention_display
from fleet.projections.ports import LiveReaders



class LiveProjection:
    reads: LiveReaders

    def with_attention(self, document: dict[str, Any], triage: dict[str, dict], items: list[dict]) -> dict[str, Any]:
        """Add stored focus choices and the Attention projection."""
        for item in items:
            if item['project_id'] and triage[item['project_id']]['policy_error']:
                item['delegable'] = False
                continue
            try:
                self.reads.attention.require_delegable(item['id'])
            except (ValueError, LookupError):
                item['delegable'] = False
            else:
                item['delegable'] = True
        return {**document, "focus": asdict(self.reads.workspace.focus_snapshot()),
                "attention": items, "triage": triage}

    def with_work(self, document: dict[str, Any]) -> dict[str, Any]:
        """Give each job and session the work item its run is linked to, or null when none is."""
        # Every store write records a history entry, so the latest sequence is the store's revision.
        # Read it before the links so a write in between is picked up by the next request.
        revision = self.reads.revision()
        cached = self.work_links
        if cached is None or cached[0] != revision:
            execution = self.reads.execution
            cached = self.work_links = (revision, self.reads.run_work(),
                                       {(run.host, run.remote_job_id): run.id for run in execution.runs()})
        links = cached[1]
        deliveries = self.reads.execution.deliveries()

        def decisions(item, run_id):
            entries = {decision['id']: decision for decision in item.get('decisions_since_dispatch', [])}
            for delivery in deliveries:
                if delivery.run == run_id and delivery.key.startswith('context-decision:'):
                    decision = json.loads(delivery.answer)
                    entries[decision['id']] = dict(decision, delivery_status=delivery.status,
                                                    delivery_error=delivery.error)
            return {'decisions_since_dispatch': list(entries.values())} if entries else {}

        return {**document, "hosts": [{**host, **{kind: [{**item, "work": links.get((host["name"], item["id"])),
                                                        "audit_run_id": cached[2].get((host["name"], item["id"])),
                                                        **decisions(item, cached[2].get((host["name"], item["id"])))}
                                                         for item in host[kind]] for kind in ("jobs", "sessions")}}
                                      for host in document["hosts"]]}

    def pipelines(self, registry: Registry, hosts: dict[str, dict[str, Any]],
                  after: int | None = None) -> list[dict[str, Any]]:
        """Declared pipelines, reported or not, and any other a host reports; with `after`, only reports since that seq.

        A declared pipeline names the room (project label) it belongs to; one nobody declared has no room.
        `host_ok` and `host_error` say whether its host is reachable now; `run` is the last report, or null."""
        with self.changed:
            reported = dict(self.pipeline_runs)
        keys = [(entry.get("host"), name) for name, entry in self.pipeline_config.items()]
        keys += [key for key in sorted(reported) if key[1] not in self.pipeline_config]
        out = []
        for host, name in keys:
            report = reported.get((host, name)) or {"run": None, "baseline": None, "seq": 0}
            if after is not None and report["seq"] <= after:
                continue
            declared = self.pipeline_config.get(name)
            label = declared.get("project") if declared else None
            project = registry.project_for(host, label) if host and label else None
            host_entry = hosts.get(host) or {"ok": False, "error": f"{host} is not a host this deck follows"}
            out.append({"host": host, "pipeline": name, "project": label, "project_id": project.id if project else None,
                        "declared": declared is not None, "host_ok": bool(host_entry.get("ok")),
                        "host_error": host_entry.get("error"), "run": report["run"], "baseline": report["baseline"],
                        "seq": report["seq"]})
        return out


def stale_work(host: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    stale = not host["ok"] or item.get("stale", False)
    return {**item, "stale": stale, "stale_reason":
            (host.get("error") or "awaiting the host’s complete snapshot") if stale else None}



def live_document(self, projects_error, capacity_error, triage, items):
    registry = self.registry
    with self.changed:
        document = self.with_attention({"time": time.time(), "project_labels": self.project_labels,
                "projects": [{"id": project_id, **entry} for project_id, entry in registry_config(registry).items()],
                "projects_error": projects_error, "hosts": [
            {**{key: value for key, value in self.by_host[host.name].items() if key not in ("jobs", "sessions") and not key.startswith("_")},
             "jobs": [annotate(self.workspace, resolve(registry, host.name, stale_work(self.by_host[host.name], job))) for job in
                      sorted(self.by_host[host.name]["jobs"].values(), key=lambda job: job.get("created_at") or 0)],
             "sessions": [annotate(self.workspace, resolve(registry, host.name, stale_work(self.by_host[host.name], session))) for session in
                          sorted(self.by_host[host.name]["sessions"].values(),
                                 key=lambda session: session.get("started_at") or 0)]}
            for host in self.hosts]}, triage, items)
    document = self.with_building(self.with_work(document), registry)
    document["building"]["capacity_error"] = capacity_error
    document["pipelines"] = self.pipelines(registry, self.by_host)
    return document


def fixture_document(self, triage, items):
    with self.changed:
        document = self.with_attention({"time": self.fixture["time"], "project_labels": self.project_labels,
                "projects": [{"id": project_id, **entry} for project_id, entry in registry_config(self.registry).items()],
                "projects_error": None, "hosts": [
            {**host, "jobs": [annotate(self.workspace, resolve(self.registry, host["name"], job)) for job in host["jobs"]],
             "sessions": [annotate(self.workspace, resolve(self.registry, host["name"], session))
                          for session in host["sessions"]]}
            for host in self.fixture["hosts"]]}, triage, items)
    document = self.with_building(self.with_work(document), self.registry)
    document["building"]["capacity_error"] = None
    document["pipelines"] = self.pipelines(self.registry, {host["name"]: host for host in self.fixture["hosts"]})
    return document


def building_document(self, document, registry):
    """Add the floors registered projects occupy within capacity with each one's focus, the projects in the
    storehouse, and the live projects that have no floor. Project work is not sent with every update: the plan
    panel reads it from /api/bench when it is open."""
    building = self.reads.building(workspace=self.workspace, registry=registry, capacity=self.capacity)
    return {**document, "building": building,
            "attention_display": attention_display(document["attention"], building, document["projects"])}
