"""A page opened before a redeploy learns of it: every state document carries the build of the files the deck serves."""
import os
from pathlib import Path

from fleet.web.server import build_id


def test_the_build_changes_when_a_served_file_changes(tmp_path: Path) -> None:
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "main.js").write_text("one")
    (tmp_path / "manifest.json").write_text("{}")
    first = build_id(tmp_path)
    assert build_id(tmp_path) == first
    (tmp_path / "js" / "main.js").write_text("two!")
    assert build_id(tmp_path) != first
    later = build_id(tmp_path)
    os.utime(tmp_path / "manifest.json", ns=(1, 1))   # a reinstall with the same sizes still moves the mtime
    assert build_id(tmp_path) != later
