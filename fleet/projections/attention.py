"""The deck's attention shape, projected from module records."""

from typing import Any

from fleet.modules.attention import AttentionFacade


def attention_items(attention: AttentionFacade, hosts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reachable = {host["name"] for host in hosts if host["ok"]}
    result = []
    for item in attention.list():
        if item.stream_context is not None:
            observed = item.stream_context
            context = {"owner": {"type": observed.owner_type, "host": observed.host, "id": observed.owner_id,
                                 "key": f"{observed.host}:{observed.owner_id}"},
                       "source": observed.source, "summary": observed.summary, "since": observed.since,
                       "project": observed.project, "project_id": observed.project_id}
            stale = observed.host not in reachable
        else:
            context = {"owner": {"type": "attention", "host": None, "id": item.id, "key": item.id},
                       "project": None, "project_id": item.project, "since": None,
                       "source": item.source, "summary": item.headline}
            stale = False
        result.append({**context, "id": item.id, "kind": item.kind, "state": item.state, "stale": stale,
                       "last_seen": item.last_seen.timestamp(), "resolution_details": item.resolution_details,
                       "acknowledged_at": item.acknowledged_at.timestamp() if item.acknowledged_at else None,
                       "resolved_at": item.resolved_at.timestamp() if item.resolved_at else None,
                       "snoozed_until": item.snooze_until.timestamp() if item.state == "snoozed" and item.snooze_until else None})
    return result
