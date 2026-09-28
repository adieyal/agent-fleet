"""Versioned fleetd permission-grant transport."""

import json

from fleet import transport
from fleet.modules.execution import GrantRequest, GrantResult


def send_grant(request: GrantRequest) -> GrantResult:
    result = transport.call(transport.host_by_name(request.host),
        ["grant", request.job, "--schema-version", "1", "--step", str(request.step), "--key", request.key],
        stdin_text=json.dumps(list(request.rules)))
    if not isinstance(result, dict) or (result.get("schema_version"), result.get("key"), result.get("status")) != (
            1, request.key, "applied") or not isinstance(result.get("continuation"), int):
        raise transport.FleetError("worker did not confirm the permission grant")
    return GrantResult(tuple(result["added"]), result["continuation"])
