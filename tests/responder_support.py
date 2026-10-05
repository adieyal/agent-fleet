"""Temporary fake executable and credentials for responder runtime tests."""
import shlex
import sys
from pathlib import Path


def fake_codex(root: Path, mode: str = 'normal') -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    binary = root / 'codex'
    script = Path(__file__).parent / 'fixtures' / 'fake_app_server.py'
    # --version must not receive the fake mode argument.
    binary.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then\n  echo "codex-cli fake-0.160.1"\nelse\n  exec '
                      + shlex.join([sys.executable, str(script)]) + ' "$@" ' + shlex.quote(mode) + '\nfi\n')
    binary.chmod(0o700)
    auth = root / 'auth.json'
    auth.write_text('{"test": true}')
    return binary, auth
