from pathlib import Path
import importlib.util
import os
import pty
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_input_spike_has_reproducible_evidence_and_conclusions() -> None:
    document = (ROOT / "docs/design/spike-input-detection-delivery.md").read_text()
    for runtime in (
        "Claude Code headless",
        "Claude Code interactive",
        "Codex exec",
        "Codex interactive",
    ):
        assert runtime in document
    for subject in ("Detection", "Delivery", "Observation shape", "Cannot detect"):
        assert subject in document
    assert "scripts/spikes/claude_input.py" in document
    assert "scripts/spikes/codex_input.py" in document
    for script in ("claude_input.py", "codex_input.py"):
        assert (ROOT / "scripts/spikes" / script).is_file()
    assert (ROOT / "scripts/checks/input_detection_delivery.py").is_file()


def test_spike_terminal_answers_queries_split_across_reads() -> None:
    spec = importlib.util.spec_from_file_location("terminal", ROOT / "scripts/spikes/terminal.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    master, slave = pty.openpty()
    try:
        terminal = module.Terminal(master, slave)
        os.set_blocking(slave, False)
        terminal.respond(b"\x1b[")
        terminal.respond(b"6n")
        assert os.read(slave, 32) == b"\x1b[1;1R"
        terminal.respond(b"\x1b[c")
        assert os.read(slave, 32) == b"\x1b[?1;2c"
        assert os.get_terminal_size(slave) == (120, 40)
        child = subprocess.run(
            [sys.executable, "-c", "import os; print(os.ttyname(os.open('/dev/tty', os.O_RDWR)))"],
            stdin=slave, capture_output=True, start_new_session=True,
            preexec_fn=module.acquire_terminal, timeout=10,
        )
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == b"/dev/tty"
    finally:
        os.close(master)
        os.close(slave)
