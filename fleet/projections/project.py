"""Persisted project work, independent of host observations."""

from dataclasses import asdict
from datetime import datetime
from typing import Any

from fleet.modules.attention import AttentionFacade
from fleet.modules.work import WorkFacade


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(entry) for key, entry in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(entry) for entry in value]
    return value


def project_status(project: str, work: WorkFacade, attention: AttentionFacade) -> dict[str, Any]:
    items = work.list(project=project)
    open_items = attention.list(project=project, state="open")
    nodes = {}
    for item in items:
        summary = work.summary(item.id)
        nodes[item.id] = {
            **asdict(item),
            "progress": asdict(work.progress(item.id)),
            "criteria": [asdict(criterion) for criterion in work.criteria(item.id)],
            "summary": asdict(summary) if summary is not None else None,
            "attention": [asdict(entry) for entry in open_items if entry.work_item == item.id],
            "children": [],
        }
    roots = []
    for item in items:
        if item.parent is None:
            roots.append(nodes[item.id])
        else:
            nodes[item.parent]["children"].append(nodes[item.id])
    return _json_value({"project": project, "work_items": roots,
                        "attention": [asdict(entry) for entry in open_items if entry.work_item is None]})
