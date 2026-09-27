#!/usr/bin/env python3
"""Probe Codex JSON/notify events and resume in an isolated temporary home."""

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
    parser.add_argument("mode", choices=("exec", "question", "resume", "interactive"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="fleet-codex-spike-") as directory:
        root = Path(directory)
        home = root / "codex"
        home.mkdir()
        shutil.copyfile(Path.home() / ".codex/auth.json", home / "auth.json")
        notify_file = root / "notify.jsonl"
        notify = root / "notify.py"
        notify.write_text("import sys\nfrom pathlib import Path\n"
                          "with Path(sys.argv[1]).open('a') as out: out.write(sys.argv[2]+'\\n')\n")
        command = ["codex", "-c", "notify=" + json.dumps(["python3", str(notify), str(notify_file)]),
                   "-c", 'approval_policy="on-request"']
        environment = dict(os.environ)
        environment["HOME"] = str(root)
        environment["CODEX_HOME"] = str(home)
        prompt = ("Ask me to choose A or B, and stop for my reply. Do not choose for me."
                  if args.mode == "question" else "Reply with exactly CODEX_FIRST. Do not use tools.")
        if args.mode == "interactive":
            command += ["-C", str(root), prompt]
            master, slave = pty.openpty()
            process = subprocess.Popen(command, cwd=root, env=environment, stdin=slave, stdout=slave,
                                       stderr=slave, start_new_session=True)
            os.close(slave)
            output = bytearray()
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline and process.poll() is None:
                if select.select([master], [], [], 0.25)[0]:
                    try:
                        output.extend(os.read(master, 65536))
                    except OSError:
                        break
                if b"CODEX_FIRST" in output and notify_file.exists():
                    break
            print("tty_tail:", output.decode(errors="replace")[-750:])
            print("waiting_at_capture:", process.poll() is None)
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            os.close(master)
            print("exit:", process.returncode)
        else:
            command += ["exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only",
                        "-C", str(root), prompt]
            first = subprocess.run(command, cwd=root, env=environment, capture_output=True,
                                   text=True, timeout=90)
            records = [json.loads(line) for line in first.stdout.splitlines() if line.startswith("{")]
            print("first_exit:", first.returncode)
            print("first_events:", [(r.get("type"), (r.get("item") or {}).get("type")) for r in records])
            print("first_text:", [(r.get("item") or {}).get("text") for r in records
                                  if (r.get("item") or {}).get("type") == "agent_message"])
            print("first_stderr:", first.stderr[-350:])
            if args.mode == "resume":
                thread = next((r.get("thread_id") for r in records if r.get("type") == "thread.started"), None)
                print("thread_id:", thread)
                if thread:
                    resumed = subprocess.run(["codex", "exec", "resume", "--json", "--skip-git-repo-check",
                                              "-c", 'sandbox_mode="read-only"', thread,
                                              "Reply with exactly CODEX_RESUMED. Do not use tools."],
                                             cwd=root, env=environment, capture_output=True, text=True,
                                             timeout=90)
                    resumed_records = [json.loads(line) for line in resumed.stdout.splitlines()
                                       if line.startswith("{")]
                    print("resume_exit:", resumed.returncode)
                    print("resume_events:", [r.get("type") for r in resumed_records])
                    print("resume_text:", [(r.get("item") or {}).get("text") for r in resumed_records
                                           if (r.get("item") or {}).get("type") == "agent_message"])
                    print("resume_stderr:", resumed.stderr[-350:])
        print("notify:", [json.loads(line).get("type") for line in notify_file.read_text().splitlines()]
              if notify_file.exists() else [])


if __name__ == "__main__":
    main()
