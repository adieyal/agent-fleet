#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: bash scripts/checks/waiting-for-input.sh PROJECT CARBON_CWD" >&2
    exit 2
fi

echo 'Prerequisites: install this fleetd on carbon; keep fleet web running to ingest observations.'
echo 'This starts a paid Claude job in default permission mode. Existing allow rules may prevent a prompt.'
uv run --frozen fleet send --host carbon --project "$1" --cwd "$2" \
    --agent claude --permission default --description 'Waiting-for-input checkpoint' \
    --step 'Use Bash to run touch "$FLEET_JOB_DIR/outbox/input-hook-check". Request permission if needed. Do not use another tool or bypass permission. Report the result.' \
    --json
echo 'After the job reports, run: uv run --frozen fleet attention list (emits JSON)'
echo 'Expect one decision sourced from runtime-input:carbon with the job ID and last_seen.'
echo 'Headless denial remains open: completion is not evidence of an answer.'
echo 'To check resumption interactively on carbon, start a fresh session with:'
echo '  claude --settings "$(python3 /path/to/installed/fleetd.py input-hook-settings --project PROJECT)"'
echo 'Ask it to run an unapproved Bash write. While the permission dialog is open, inspect attention list.'
echo 'Approve in that terminal; the matching PostToolUse resolves it as "answered in session".'
echo 'Disconnecting the host while waiting must leave the item open with its recorded last_seen.'
