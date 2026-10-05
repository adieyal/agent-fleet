"""Versioned fleetd input command transport."""

from fleet import transport
from fleet.modules.execution import Delivery, InputResult, Run


def send_input(run: Run, delivery: Delivery) -> InputResult:
    try:
        command = "receive-decision" if delivery.key.startswith("context-decision:") else "deliver"
        result = transport.call(transport.host_by_name(run.host),
            [command, run.remote_job_id, "--schema-version", "1", "--key", delivery.key],
            stdin_text=delivery.answer)
        if result == {"schema_version": 1, "key": delivery.key, "status": "busy"}:
            return InputResult("busy")
        if result != {"schema_version": 1, "key": delivery.key, "status": "applied"}:
            return InputResult("failed", "worker did not acknowledge the delivery key")
    except (transport.FleetError, OSError, ValueError) as error:
        return InputResult("failed", str(error))
    return InputResult("applied")
