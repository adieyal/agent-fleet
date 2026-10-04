"""Storage accounting for retained documents and traces."""
from pathlib import Path


def usage(store, root: Path) -> dict:
    value = store.usage()
    for name, directory in (("documents", root / "projects"), ("traces", root / "traces")):
        files = [path for path in directory.rglob("*") if path.is_file() and not path.is_symlink()]
        value[name] = {"path": str(directory), "files": len(files), "bytes": sum(path.stat().st_size for path in files)}
    return value
