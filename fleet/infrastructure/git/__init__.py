"""Process-serialized management repository writer."""

import fcntl
import hashlib
import subprocess
from contextlib import contextmanager
from pathlib import Path


class RepositoryWriter:
    def git(self, root, *args: str) -> str:
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise ValueError(result.stderr.strip())
        return result.stdout.strip()

    def root(self, path) -> str:
        root = str(Path(path).resolve())
        if self.git(root, 'rev-parse', '--show-toplevel') != root:
            raise ValueError('management repository must be a Git working tree root')
        return root

    def validate_path(self, root: str, path: str) -> None:
        relative = Path(path)
        if relative.is_absolute() or '..' in relative.parts or '.git' in relative.parts or not relative.parts:
            raise ValueError('invalid record path')
        if not (Path(root) / relative).resolve().is_relative_to(Path(root)):
            raise ValueError('record path escapes repository')

    @contextmanager
    def lock(self, root: str):
        directory = self.git(root, 'rev-parse', '--absolute-git-dir')
        with (Path(directory) / 'fleet-writer.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def read(self, root: str, path: str, revision: str) -> str:
        result = subprocess.run(['git', '-C', root, 'show', f'{revision}:{path}'],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise ValueError(result.stderr.strip())
        return result.stdout

    def find(self, root: str, intent: dict) -> str | None:
        revisions = self.git(root, 'log', '--all', '--format=%H', '--fixed-strings',
                             '--grep=Fleet-Intent: ' + intent['id']).splitlines()
        for revision in revisions:
            body = self.read(root, intent['path'], revision)
            if hashlib.sha256(body.encode()).hexdigest() == intent['digest']:
                return revision
        return None

    def commit(self, root: str, intent: dict, body: str) -> str:
        if self.git(root, 'status', '--porcelain'):
            raise ValueError('management repository has uncommitted changes')
        path = Path(root) / intent['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        self.git(root, 'add', '--', intent['path'])
        message = f"Author {intent['path']}\n\nFleet-Intent: {intent['id']}\nActor: {intent['actor']}"
        if intent['source_run'] is not None:
            message += f"\nSource-Run: {intent['source_run']}"
        self.git(root, '-c', 'user.name=' + intent['actor'], '-c', 'user.email=fleet@localhost',
                 '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', message)
        revision = self.find(root, intent)
        if revision is None:
            raise ValueError('commit could not be confirmed')
        return revision
