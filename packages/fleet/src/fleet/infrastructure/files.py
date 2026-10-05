"""Local filesystem operations for context transfers and storage accounting."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory


class LocalFiles:
    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def absolute(self, path: str) -> str:
        return os.path.abspath(path)

    def expand_user(self, path: str) -> str:
        return os.path.expanduser(path)

    def temporary_directory(self, *, prefix: str) -> TemporaryDirectory:
        return TemporaryDirectory(prefix=prefix)

    def find_reference(self, reference: str) -> Path | None:
        candidates = [Path(reference).expanduser()]
        if not candidates[0].is_absolute():
            candidates = [Path.cwd() / reference]
            if os.environ.get("FLEET_JOB_ID"):
                candidates.append(Path("~/.fleet/jobs").expanduser() / os.environ["FLEET_JOB_ID"] / reference)
        return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)

    def create_directory(self, destination: str) -> Path:
        target = Path(destination).resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target

    def usage(self, directory: Path) -> dict:
        files = [path for path in directory.rglob("*") if path.is_file() and not path.is_symlink()]
        return {"path": str(directory), "files": len(files), "bytes": sum(path.stat().st_size for path in files)}
