#!/usr/bin/env bash
# Usage: scripts/checks/answer-delivery.sh <stopped-Claude-job-on-carbon>
set -euo pipefail
cd "$(dirname "$0")/../.."
uv run --frozen python - "$@" <<'PY'
import sys
import time
from uuid import uuid4
from fleet import transport

if len(sys.argv) != 2:
    raise SystemExit("Supply a stopped Fleet Claude job ID on carbon; install this fleetd there first.")
host = transport.host_by_name("carbon")
job = sys.argv[1]
before = transport.call(host, ["show", job])
assert before["agent"] == "claude" and before["session_id"], before
key = str(uuid4())
marker = "ANSWER_RECEIVED_" + key
answer = f"Reply with exactly {marker}, then FLEET_STATUS: done. Do not use tools."
command = ["deliver", job, "--schema-version", "1", "--key", key]
first = transport.call(host, command, stdin_text=answer)
second = transport.call(host, command, stdin_text=answer)
assert first == second == {"schema_version": 1, "key": key, "status": "applied"}
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    after = transport.call(host, ["show", job])
    steps = [step for step in after["steps"] if step.get("delivery_key") == key]
    assert len(steps) == 1 and after["session_id"] == before["session_id"]
    if steps[0]["status"] in ("done", "failed"):
        assert steps[0]["status"] == "done" and marker in steps[0]["result"], steps[0]
        print(f"Same Claude session received one answer step: {key}")
        break
    time.sleep(1)
else:
    raise SystemExit("Delivery did not finish within 90 seconds; inspect the job on carbon.")
PY
