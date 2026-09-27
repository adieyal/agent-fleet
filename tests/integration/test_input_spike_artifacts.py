from pathlib import Path


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
