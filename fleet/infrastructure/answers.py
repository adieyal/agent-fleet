"""Versioned fleetd transport for answering a blocked job step."""

import json

from fleet import transport
from fleet.modules.execution import AnswerRequest


def send_answer(request: AnswerRequest) -> int:
    """Add the reply as a step that answers the blocked one, through the same fleetd add `fleet add` uses."""
    result = transport.call(transport.host_by_name(request.host),
        ["add", request.job, "--steps-file", "/dev/stdin", "--schema-version", "1", "--key", request.key,
         "--answers", str(request.step)],
        stdin_text=json.dumps([{"prompt": request.reply, "title": f"Answer to step {request.step + 1}"}]))
    if not isinstance(result, dict) or (result.get("schema_version"), result.get("key"), result.get("status"),
                                        result.get("answers")) != (1, request.key, "applied", request.step) or not (
            isinstance(result.get("steps"), list) and len(result["steps"]) == 1 and isinstance(result["steps"][0], int)):
        raise transport.FleetError("worker did not confirm the answer")
    return result["steps"][0]
