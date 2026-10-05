"""Storage accounting for retained documents and traces."""
from pathlib import Path
from typing import Protocol


class StorageFiles(Protocol):
    def usage(self, directory: Path) -> dict: ...


def usage(store, root: Path, files: StorageFiles) -> dict:
    value = store.usage()
    for name, directory in (("documents", root / "projects"), ("traces", root / "traces")):
        value[name] = files.usage(directory)
    return value
