from fleet.projections.attention import attention_display


def test_markers_use_stored_attention_and_project_identity():
    items = [{"id": str(index), "project": None, "project_id": "p1", "kind": kind, "state": state}
             for index, (kind, state) in enumerate([
                 ("alert", "open"), ("decision", "open"), ("blocker", "snoozed"), ("alert", "resolved")])]
    building = {"floors": {"p1": 1, "busy": 2}, "shuttered": {}}
    projects = [{"id": "p1", "links": [{"label": "room"}]}]
    display = attention_display(items, building, projects)
    [marker] = display["places"]
    assert (marker["place"], marker["count"], marker["glyph"], marker["kind"]) == (1, 2, "✱", "decision")
    assert display["rooms"]["room"]["listed"] == ["0", "1", "2"]
    assert display["front_desk"] == ["0", "1"]
    assert display["open_count"] == 2
    items[0]["state"] = items[1]["state"] = "acknowledged"
    display = attention_display(items, building, projects)
    assert display["places"][0]["level"] == "acknowledged"
    assert display["places"][0]["open_ids"] == []
    building = {"floors": {"busy": 2}, "shuttered": {"p1": {}}}
    assert [row["place"] for row in attention_display(items, building, projects)["places"]] == ["lobby", "store"]
