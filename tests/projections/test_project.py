from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fleet.modules.attention import AttentionFacade, AttentionItem
from fleet.modules.work import Criterion, EvidenceSpecification, Summary, WorkFacade, WorkItem
from fleet.projections.project import project_status


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


class ReadWork:
    def __init__(self, items, criteria=(), summaries=()):
        self.records = {"item": items, "criterion": criteria, "summary": summaries}

    def list(self, kind):
        return self.records[kind]

    def get(self, kind, identity):
        return next(item for item in self.list(kind) if item.id == identity)


class ReadAttention:
    def __init__(self, items):
        self.items = items

    def list(self):
        return self.items


def item(identity, **changes):
    return replace(WorkItem(identity, "p", None, "task", identity, "Deliver", "none",
                            None, None, None, NOW, NOW), **changes)


def project(items, criteria=(), summaries=(), attention=()):
    # These ports expose reads only; any attempted write fails.
    work = WorkFacade(ReadWork(items, criteria, summaries), None, lambda: NOW)
    alerts = AttentionFacade(ReadAttention(attention), lambda: NOW)
    return project_status("p", work, alerts)


def test_progress_precedence_counts_only_direct_milestones_and_preserves_unknown():
    items = [item("epic", kind="epic"), item("done", parent="epic", kind="milestone", condition="complete"),
             item("planned", parent="epic", kind="milestone"),
             item("task", parent="epic", condition="complete"), item("unknown"),
             item("other", project="other")]
    criteria = [Criterion("c1", "epic", "Ignored for progress", "accepted", None, state="met"),
                Criterion("c2", "planned", "Reviewed", "judged", None, state="met"),
                Criterion("c3", "planned", "Approved", "accepted", None),
                Criterion("c4", "planned", "Tests", "checked", EvidenceSpecification("report"))]
    result = project(items, criteria)
    epic, unknown = result["work_items"]
    assert epic["progress"] == {"basis": "milestones", "complete": 1, "total": 2}
    planned = epic["children"][1]
    assert planned["progress"] == {"basis": "criteria", "complete": 1, "total": 3}
    assert epic["children"][2]["progress"] == {"basis": "unknown", "complete": None, "total": None}
    assert [c["verification"] for c in planned["criteria"]] == ["judged", "accepted", "checked"]
    assert unknown["progress"] == {"basis": "unknown", "complete": None, "total": None}
    assert unknown["summary"] is None
    assert unknown["next_step"] is None


def test_tree_summary_and_linked_open_attention_are_read_only():
    items = [item("root", kind="epic")]
    for depth in range(4):
        items.append(item(str(depth), parent=items[-1].id))
    items[-1] = replace(items[-1], condition="blocked", next_step="Ask user")
    summary = Summary("3", "Purpose", "Done", "Doing", "Next", "user", NOW)
    blocker = AttentionItem("a", "p", "3", None, "blocker", "user", "work", "3",
                            "Choose supplier", "work:3", "open", None, None, NOW)
    attention = [blocker, replace(blocker, id="resolved", state="resolved"),
                 replace(blocker, id="project", work_item=None),
                 replace(blocker, id="expired", state="snoozed", snooze_until=NOW-timedelta(days=1)),
                 replace(blocker, id="later", state="snoozed", snooze_until=NOW+timedelta(days=1)),
                 replace(blocker, id="other", project="other")]
    result = project(items, summaries=[summary], attention=attention)
    node = result["work_items"][0]
    for _ in range(4):
        node, = node["children"]
    assert node["condition"] == "blocked"
    assert node["next_step"] == "Ask user"
    assert node["summary"]["authoring_role"] == "user"
    assert node["summary"]["updated"] == NOW.isoformat()
    assert [a["id"] for a in node["attention"]] == ["a", "expired"]
    assert [a["id"] for a in result["attention"]] == ["project"]
    assert attention[3].state == "snoozed"


def test_empty_project_has_no_invented_progress():
    assert project([]) == {"project": "p", "work_items": [], "attention": []}
