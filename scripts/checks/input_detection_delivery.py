#!/usr/bin/env python3
"""Print real-runtime FS-003 evidence for review on an authenticated host."""

from pathlib import Path
import subprocess
import sys


def main() -> None:
    spikes = Path(__file__).resolve().parents[1] / "spikes"
    for runtime, modes in (
        ("claude", ("headless", "resume", "interactive")),
        ("codex", ("exec", "question", "resume", "interactive")),
    ):
        for mode in modes:
            command = [sys.executable, str(spikes / f"{runtime}_input.py"), mode]
            print("COMMAND:", " ".join(command), flush=True)
            subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
