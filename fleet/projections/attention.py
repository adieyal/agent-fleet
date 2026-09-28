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
                       "context_reference": item.context_reference,
                       "last_seen": item.last_seen.timestamp(), "resolution_details": item.resolution_details,
                       "acknowledged_at": item.acknowledged_at.timestamp() if item.acknowledged_at else None,
                       "resolved_at": item.resolved_at.timestamp() if item.resolved_at else None,
                       "snoozed_until": item.snooze_until.timestamp() if item.state == "snoozed" and item.snooze_until else None})
    return result


def attention_display(items: list[dict], building: dict, projects: list[dict]) -> dict:
    places: dict[int | str, list[dict]] = {}
    rooms: dict[str, list[dict]] = {}
    for item in items:
        labels = {item["project"]} if item["project"] is not None else {
            link["label"] for project in projects if project["id"] == item["project_id"] for link in project["links"]}
        for label in labels:
            rooms.setdefault(label, []).append(item)
        if item["state"] not in ("open", "acknowledged"):
            continue
        project = item["project_id"]
        locations = [building["floors"][project]] if project in building["floors"] else (
            ["lobby", "store"] if project in building["shuttered"] else ["lobby"])
        for place in locations:
            places.setdefault(place, []).append(item)

    def marker(rows: list[dict]) -> dict:
        shown = [row for row in rows if row["state"] in ("open", "acknowledged")]
        opened = [row["id"] for row in shown if row["state"] == "open"]
        kind = next((kind for kind in ("blocker", "decision", "alert") if any(row["kind"] == kind for row in shown)), None)
        return {"count": len(shown), "level": "open" if opened else "acknowledged" if shown else None,
                "kind": kind, "glyph": "✱", "open_ids": opened,
                "shown": [row["id"] for row in shown],
                "listed": [row["id"] for row in rows if row["state"] != "resolved"]}

    return {"places": [{"place": place, **marker(rows)} for place, rows in places.items()],
            "rooms": {label: marker(rows) for label, rows in rooms.items()},
            "front_desk": [row["id"] for row in items if row["state"] in ("open", "acknowledged")],
            "open_count": sum(row["state"] == "open" for row in items)}
