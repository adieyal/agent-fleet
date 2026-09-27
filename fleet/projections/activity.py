"""Attach Execution's activity classification to deck events."""

from fleet.modules.execution import ExecutionFacade


def event_activity(event: dict | None) -> dict | None:
    if event is None:
        return None
    return {**event, "activity_class": ExecutionFacade.classify_activity(event)}


def with_activity(item: dict) -> dict:
    result = dict(item)
    if "activity" in item:
        result["activity"] = event_activity(item["activity"])
    if "events" in item:
        result["events"] = [event_activity(event) for event in item["events"]]
    return result
