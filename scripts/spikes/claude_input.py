#!/usr/bin/env python3
"""Probe Claude hook events and headless resume in an isolated temporary home."""

import argparse
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import tempfile
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("headless", "resume", "interactive"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="fleet-claude-spike-") as directory:
        root = Path(directory)
        config = root / "claude"
        config.mkdir()
        credentials = Path.home() / ".claude/.credentials.json"
        if credentials.is_file():
            shutil.copyfile(credentials, config / ".credentials.json")
        state = Path.home() / ".claude.json"
        if state.is_file():
            shutil.copyfile(state, root / ".claude.json")
        hook = root / "hook.py"
        hook.write_text(
            "import json,sys\n"
            "from pathlib import Path\n"
            "data=json.load(sys.stdin)\n"
            "with Path(sys.argv[1]).open('a') as out: out.write(json.dumps(data)+'\\n')\n"
        )
        events = root / "hooks.jsonl"
        settings = root / "settings.json"
        settings.write_text(json.dumps({"hooks": {
            name: [{"hooks": [{"type": "command", "command": f"python3 {hook} {events}"}]}]
            for name in ("Notification", "Stop", "PermissionRequest")
        }}))
        environment = dict(os.environ)
        environment["HOME"] = str(root)
        environment["CLAUDE_CONFIG_DIR"] = str(config)
        for key in list(environment):
            if key in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID"):
                del environment[key]
        auth = subprocess.run(["claude", "auth", "status"], cwd=root, env=environment,
                              capture_output=True, text=True, timeout=10)
        print("auth:", auth.stdout[:200])
        prompt = (
            "Use the Bash tool to run touch marker.txt exactly once in this temporary directory. "
            "Then report whether it worked."
            if args.mode != "resume" else "Reply with the exact word RESUMED."
        )
        command = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
                   "--include-hook-events", "--permission-mode", "manual", "--settings", str(settings),
                   "--setting-sources", "", "--model", "haiku"]
        if args.mode == "interactive":
            command = ["claude", "--settings", str(settings), "--setting-sources", "", "--model", "haiku"]
        if args.mode == "resume":
            first = subprocess.run(command[:], cwd=root, env=environment, capture_output=True,
                                   text=True, timeout=90)
            session_id = next((json.loads(line).get("session_id") for line in first.stdout.splitlines()
                               if line.startswith('{') and json.loads(line).get("session_id")), None)
            print("first_exit:", first.returncode, "session_id:", session_id)
            if not session_id:
                print("first_stderr:", first.stderr[-500:])
                return
            command = ["claude", "-p", "Reply with the exact word RESUMED.", "--resume", session_id,
                       "--output-format", "stream-json", "--verbose", "--settings", str(settings),
                       "--setting-sources", "", "--model", "haiku"]
        if args.mode == "interactive":
            master, slave = pty.openpty()
            process = subprocess.Popen(command, cwd=root, env=environment, stdin=slave, stdout=slave,
                                       stderr=slave, start_new_session=True)
            os.close(slave)
            output = bytearray()
            sent = False
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.25)[0]:
                    try:
                        output.extend(os.read(master, 65536))
                    except OSError:
                        break
                if not sent and time.monotonic() > deadline - 40:
                    os.write(master, b"Use Bash to run touch marker.txt.\r")
                    sent = True
                if events.exists() and "PermissionRequest" in events.read_text():
                    break
                if process.poll() is not None:
                    break
            print("tty_tail:", output.decode(errors="replace")[-1200:])
            print("waiting_at_capture:", process.poll() is None)
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            os.close(master)
            class Result:
                returncode = process.returncode
                stdout = ""
                stderr = ""
            result = Result()
        else:
            result = subprocess.run(command, cwd=root, env=environment, capture_output=True,
                                    text=True, timeout=90)
        print("exit:", result.returncode)
        records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        print("stream:", [(r.get("type"), r.get("subtype"), r.get("session_id")) for r in records])
        print("result:", [r.get("result") for r in records if r.get("type") == "result"])
        print("tools:", [(b.get("name"), b.get("input")) for r in records if r.get("type") == "assistant"
                         for b in r.get("message", {}).get("content", []) if b.get("type") == "tool_use"])
        print("hooks:", [(r.get("hook_event_name"), r.get("notification_type"),
                           r.get("permission_mode")) for r in map(json.loads, events.read_text().splitlines())]
              if events.exists() else [])
        print("stderr:", result.stderr[-500:])


if __name__ == "__main__":
    main()
