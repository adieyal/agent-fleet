"""Create a dedicated lean Codex profile without modifying user credentials."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from fleet.errors import FleetError


@dataclass(frozen=True)
class ResponderEnvironment:
    binary: str
    codex_home: Path
    home: Path

    def environ(self) -> dict[str, str]:
        # Keep only process/network essentials. Parent job IDs, repo instruction
        # overrides, API keys and Codex configuration must not leak into turns.
        keys = ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR", "SSL_CERT_FILE",
                "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "all_proxy", "no_proxy")
        return {**{key: os.environ[key] for key in keys if key in os.environ},
                "HOME": str(self.home), "CODEX_HOME": str(self.codex_home)}


def provision(fleet_home: Path, *, auth: Path | None = None,
              binary: str = "codex") -> ResponderEnvironment:
    executable = shutil.which(binary)
    if executable is None:
        raise FleetError(f"responder unavailable: codex binary missing: {binary}")
    source = auth or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    source = source.expanduser().absolute()
    if not source.is_file() or not os.access(source, os.R_OK):
        raise FleetError(f"responder unavailable: codex auth missing or unreadable: {source}")
    root = fleet_home.expanduser().resolve() / "responder"
    codex_home, home = root / "codex", root / "home"
    for directory in (root, codex_home, home):
        if directory.is_symlink():
            raise FleetError(f"responder directory must not be a symlink: {directory}")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    # Codex may write its own caches here; reject user config/skills rather than
    # deleting unexpected data or silently discovering extra instructions.
    if any((home / name).exists() or (home / name).is_symlink()
           for name in (".codex", ".agents", ".claude", "AGENTS.md", "AGENTS.override.md")):
        raise FleetError(f"responder HOME contains instruction/config directories: {home}")
    if any((codex_home / name).exists() or (codex_home / name).is_symlink()
           for name in ("skills", "AGENTS.md", "AGENTS.override.md")):
        raise FleetError(f"responder CODEX_HOME contains extra instructions: {codex_home}")
    target = codex_home / "auth.json"
    if target.exists() or target.is_symlink():
        if not target.is_symlink() or target.resolve() != source.resolve():
            raise FleetError(f"responder auth path already exists with another source: {target}")
    else:
        target.symlink_to(source)
    resources = files("fleet.infrastructure.codex")
    instructions = codex_home / "instructions.md"
    config_path = codex_home / "config.toml"
    for path in (instructions, config_path):
        if path.is_symlink():
            raise FleetError(f"responder profile file must not be a symlink: {path}")
    instructions.write_text(resources.joinpath("instructions.md").read_text(), encoding="utf-8")
    template = resources.joinpath("lean-config.toml").read_text()
    config = template.replace('"__INSTRUCTIONS_PATH__"', json.dumps(str(instructions)))
    config_path.write_text(config, encoding="utf-8")
    return ResponderEnvironment(executable, codex_home, home)
