from dataclasses import replace

import pytest

from fleet.modules.attention import AttentionItem
from fleet.projections.bench import bench_rooms, headline, status

from projections.test_project import NOW, item, project

MILESTONES = ["1. Inventory routes", "2. Shared layout", "3. Auth pages", "4. Supplier pages",
              "5. Order pages", "6. Remove legacy router"]


def route_migration():
    items = [item("overhaul", kind="epic", title="V2 frontend overhaul", goal="Rebuild the V2 frontend."),
             item("routes", kind="epic", parent="overhaul", title="Route migration",
                  goal="Move every page to the new router. Legacy routes go last.\nSource: plan.md")]
    for index, title in enumerate(MILESTONES, 1):
        items.append(item(f"m{index}", kind="milestone", parent="routes", title=title,
                          condition="complete" if index <= 3 else "blocked" if index == 6 else "none",
                          next_step={4: "Port supplier list", 6: "Delete router.js"}.get(index)))
    items += [item("m4-task", parent="m4", title="Port supplier list"),
              item("loose", parent="routes", title="Fix redirect loop"),
              item("parked", parent="routes", title="Tidy styles", condition="on hold"),
              item("design", kind="milestone", parent="overhaul", title="Design tokens")]
    decision = AttentionItem("d", "p", "m5", None, "decision", "user", "work", "m5",
                             "Keep order URLs?", "work:m5", "open", None, None, NOW)
    attention = [decision, replace(decision, id="b", work_item="loose", kind="blocker", headline="Redirect loops"),
                 replace(decision, id="alert", work_item="m4", kind="alert", headline="Disk low")]
    doc = project(items, attention=attention)
    overhaul, = doc["work_items"]
    routes = overhaul["children"][0]
    running = {"id": "r1", "host": "home", "status": "running"}
    routes["children"][3]["children"][0]["runs"] = [running, {**running, "id": "r0", "status": "failed"}]
    routes["children"][6]["runs"] = [{**running, "id": "r2", "host": "worker"}]
    return doc


def test_epic_rooms_summarise_nested_route_migration():
    parent, child = bench_rooms(route_migration())["rooms"]
    assert (parent["title"], parent["depth"], parent["parent"]) == ("V2 frontend overhaul", 0, None)
    assert parent["children"] == [{"id": "routes", "title": "Route migration"}]
    assert parent["milestones"] == {"complete": 0, "total": 1}
    assert [agent["run"] for agent in parent["agents"]] == ["r1", "r2"]
    assert len(parent["attention"]) == 2

    assert child["parent"] == {"id": "overhaul", "title": "V2 frontend overhaul"}
    assert child["depth"] == 1
    assert child["headline"] == "Move every page to the new router."
    assert child["goal"].endswith("Source: plan.md")
    assert child["milestones"] == {"complete": 3, "total": 6}
    assert child["agents"] == [
        {"run": "r1", "host": "home", "work_item": "m4-task", "title": "Port supplier list"},
        {"run": "r2", "host": "worker", "work_item": "loose", "title": "Fix redirect loop"}]
    assert child["upcoming"] == [
        {"id": "m4", "title": "4. Supplier pages", "next_step": "Port supplier list"},
        {"id": "m5", "title": "5. Order pages", "next_step": None},
        {"id": "m6", "title": "6. Remove legacy router", "next_step": "Delete router.js"}]
    assert child["children"] == []
    assert [(a["id"], a["kind"], a["work_item"]) for a in child["attention"]] == [
        ("d", "decision", "m5"), ("b", "blocker", "loose")]
    assert [bench["title"] for bench in child["benches"]] == MILESTONES


def test_epic_page_lists_milestones_then_direct_tasks_with_status():
    parent, child = bench_rooms(route_migration())["rooms"]
    assert [(m["title"], m["status"]) for m in child["plan"]] == [
        ("1. Inventory routes", "complete"), ("2. Shared layout", "complete"), ("3. Auth pages", "complete"),
        ("4. Supplier pages", "active"), ("5. Order pages", "next"), ("6. Remove legacy router", "blocked")]
    assert child["plan"][4]["next_step"] is None
    assert child["plan"][0]["headline"] == "Deliver"
    assert [(t["id"], t["status"]) for t in child["tasks"]] == [("loose", "active"), ("parked", "on hold")]
    assert child["progress"] == {"basis": "milestones", "complete": 3, "total": 6}
    assert child["criteria"] == []
    assert [m["id"] for m in parent["plan"]] == ["design"]
    assert parent["tasks"] == []


@pytest.mark.parametrize("condition, running, expected", [
    ("complete", True, "complete"), ("blocked", True, "blocked"), ("on hold", False, "on hold"),
    ("waiting", True, "on hold"), ("ready for review", False, "active"), ("none", True, "active"),
    ("none", False, "next")])
def test_status_prefers_recorded_condition_over_runs(condition, running, expected):
    milestone = item("m", kind="milestone", condition=condition,
                     resume_condition="Data arrives" if condition == "waiting" else None)
    node, = project([milestone, item("t", parent="m")])["work_items"]
    node["children"][0]["runs"] = [{"status": "running" if running else "failed"}]
    assert status(node) == expected


def test_epic_without_milestones_or_work_records_nothing():
    room, = bench_rooms(project([item("epic", kind="epic", goal="Explore")]))["rooms"]
    assert room["milestones"] == {"complete": 0, "total": 0}
    assert room["agents"] == room["upcoming"] == room["attention"] == room["children"] == room["workstreams"] == []


def test_headline_is_the_first_sentence_of_the_first_line():
    assert headline("Ship it! Then rest.") == "Ship it!"
    assert headline("Version 2.1 migration\nSource: a.md") == "Version 2.1 migration"


def supplier_migration():
    """V2 frontend overhaul: a milestone of its own, then workstream Supplier migration at 6 of 7."""
    items = [item("overhaul", kind="epic", title="V2 frontend overhaul"),
             item("suppliers", kind="workstream", parent="overhaul", title="Supplier migration"),
             item("design", kind="milestone", parent="overhaul", title="Design tokens"),
             item("empty", kind="workstream", parent="overhaul", title="Order migration")]
    items += [item(f"s{n}", kind="milestone", parent="suppliers", title=f"Slice {n}: {topic}",
                   condition="none" if n == 6 else "complete", next_step="Map CSV columns" if n == 6 else None)
              for n, topic in enumerate(["routes", "list", "detail", "search", "exports", "supplier imports",
                                         "cleanup"], 1)]
    items.append(item("s6-task", parent="s6", title="Parse CSV"))
    return project(items)


def test_workstreams_are_named_with_their_own_milestones_and_next():
    room, = bench_rooms(supplier_migration())["rooms"]
    suppliers, empty = room["workstreams"]
    assert (suppliers["title"], suppliers["milestones"]) == ("Supplier migration", {"complete": 6, "total": 7})
    assert suppliers["next"] == {"id": "s6", "title": "Slice 6: supplier imports", "next_step": "Map CSV columns"}
    assert [m["title"] for m in suppliers["plan"]][5:] == ["Slice 6: supplier imports", "Slice 7: cleanup"]
    assert (empty["title"], empty["milestones"], empty["next"], empty["plan"]) == (
        "Order migration", {"complete": 0, "total": 0}, None, [])
    assert [m["id"] for m in room["plan"]] == ["design"]
    assert room["milestones"] == {"complete": 6, "total": 8}
    assert [m["id"] for m in room["upcoming"]] == ["design", "s6"]


def test_a_finished_workstream_has_no_next_milestone():
    items = [item("epic", kind="epic"), item("ws", kind="workstream", parent="epic"),
             item("m", kind="milestone", parent="ws", condition="complete")]
    room, = bench_rooms(project(items))["rooms"]
    assert room["workstreams"][0]["milestones"] == {"complete": 1, "total": 1}
    assert room["workstreams"][0]["next"] is None
