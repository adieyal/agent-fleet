from dataclasses import replace

from fleet.modules.attention import AttentionItem
from fleet.projections.bench import bench_rooms, headline

from projections.test_project import NOW, item, project

MILESTONES = ["1. Inventory routes", "2. Shared layout", "3. Auth pages", "4. Supplier pages",
              "5. Order pages", "6. Remove legacy router"]


def route_migration():
    items = [item("overhaul", kind="epic", title="V2 frontend overhaul", goal="Rebuild the V2 frontend."),
             item("routes", kind="epic", parent="overhaul", title="Route migration",
                  goal="Move every page to the new router. Legacy routes go last.\nSource: plan.md")]
    for index, title in enumerate(MILESTONES, 1):
        items.append(item(f"m{index}", kind="milestone", parent="routes", title=title,
                          condition="complete" if index <= 3 else "none",
                          next_step={4: "Port supplier list", 6: "Delete router.js"}.get(index)))
    items += [item("m4-task", parent="m4", title="Port supplier list"),
              item("loose", parent="routes", title="Fix redirect loop"),
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


def test_epic_without_milestones_or_work_records_nothing():
    room, = bench_rooms(project([item("epic", kind="epic", goal="Explore")]))["rooms"]
    assert room["milestones"] == {"complete": 0, "total": 0}
    assert room["agents"] == room["upcoming"] == room["attention"] == room["children"] == []


def test_headline_is_the_first_sentence_of_the_first_line():
    assert headline("Ship it! Then rest.") == "Ship it!"
    assert headline("Version 2.1 migration\nSource: a.md") == "Version 2.1 migration"
