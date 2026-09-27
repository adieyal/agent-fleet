"""Versioned fleetd input command transport."""

from fleet import transport
from fleet.modules.execution import Delivery, Run


def send_input(run: Run, delivery: Delivery) -> str | None:
    try:
        result = transport.call(transport.host_by_name(run.host),
            ["deliver", run.remote_job_id, "--schema-version", "1", "--key", delivery.key],
            stdin_text=delivery.answer)
        if result != {"schema_version": 1, "key": delivery.key, "status": "applied"}:
            return "worker did not acknowledge the delivery key"
    except (transport.FleetError, OSError, ValueError) as error:
        return str(error)
    return None
