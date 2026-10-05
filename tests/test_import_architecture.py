"""Exercise the import contracts against isolated copies of the production package."""

from configparser import ConfigParser
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import grimp


ROOT = Path(__file__).resolve().parents[1]


def test_every_python_file_is_in_the_import_graph():
    graph = grimp.build_graph("fleet", cache_dir=None)
    for source in (ROOT / "fleet").rglob("*.py"):
        parts = source.relative_to(ROOT).with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        assert module in graph.modules, f"{source} needs a discoverable package"


def test_every_business_module_has_a_public_surface_contract():
    config = ConfigParser()
    config.read(ROOT / ".importlinter")
    protected = {
        section["protected_modules"]: section["allowed_importers"]
        for section in config.values() if section.get("type") == "protected"
    }
    for package in (ROOT / "fleet/modules").glob("*/__init__.py"):
        module = f"fleet.modules.{package.parent.name}"
        assert protected[f"{module}.*"] == module


def lint_copy(tmp_path: Path, filename: str = "", statement: str = "") -> subprocess.CompletedProcess:
    for source in (ROOT / "fleet").rglob("*.py"):
        target = tmp_path / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shutil.copyfile(ROOT / ".importlinter", tmp_path / ".importlinter")
    if filename:
        with (tmp_path / filename).open("a") as target:
            target.write(f"\n{statement}\n")
    return subprocess.run(
        [str(Path(sys.executable).with_name("lint-imports")), "--no-cache"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True, text=True, timeout=10,
    )


def test_production_import_contracts(tmp_path):
    result = lint_copy(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("filename", "statement", "contract"), [
    ("fleet/web/server.py", "import fleet.transport", "Controllers use public providers and module surfaces"),
    ("fleet/web/server.py", "import fleet.services.jobs", "Controllers use public providers and module surfaces"),
    ("fleet/container.py", "import fleet_web.server", "Container has no presentation dependencies"),
    ("fleet/services/jobs.py", "import fleet.container", "Document library independence"),
    ("fleet/projections/project.py", "import fleet.container", "Controller layers"),
    ("fleet/container.py", "import fleet_cli.cli", "Controller layers"),
    ("fleet/cli.py", "import fleet.infrastructure.sqlite", "Infrastructure construction"),
    ("fleet/cli.py", "import fleet.infrastructure.documents.evidence", "Infrastructure construction"),
    ("fleet/web/server.py", "import fleet.infrastructure.sqlite", "Infrastructure construction"),
    ("fleet/modules/work/__init__.py", "import fleet.infrastructure", "Module independence"),
    ("fleet/modules/work/__init__.py", "import fleet.projections", "Module independence"),
    ("fleet/cli.py", "import fleet.modules.work.domain", "Work public surface"),
    ("fleet/modules/library/facade.py", "import fleet.modules.work.application", "Work public surface"),
    ("fleet/container.py", "import fleet.modules.work.facade", "Work public surface"),
    ("fleet/modules/work/domain/__init__.py", "import fleet.modules.work.application", "Module domain layers"),
    ("fleet/remote/fleetd.py", "import fleet_cli.cli", "Standalone worker"),
    ("fleet/remote/fleetd.py", "import fleet", "Standalone worker"),
    ("fleet/remote/fleetd.py", "import fleet.remote", "Standalone worker"),
    ("fleet/infrastructure/documents/job_store.py", "import fleet_web.documents", "Document library independence"),
    ("fleet/services/documents.py", "import fleet.container", "Document library independence"),
    ("fleet/services/documents.py", "import fleet.infrastructure.documents.job_store", "Service infrastructure separation"),
    ("fleet/ingestion.py", "import fleet_web.server", "Document library independence"),
    ("fleet/modules/work/__init__.py", "import fleet.ingestion", "Module independence"),
    ("fleet/modules/work/__init__.py", "import fleet.services", "Module independence"),
    ("fleet/web/server.py", "import subprocess", "Web processes use transport"),
    ("fleet/cli.py", "import subprocess", "CLI processes use transport"),
    ("fleet/cli.py", "import fleet_web.documents", "Controller layers"),
    ("fleet/web/server.py", "import fleet_cli.cli", "Controller layers"),
    ("fleet/triage.py", "import fleet.container", "Controller layers"),
    ("fleet/triage_scheduler.py", "import fleet_web.server", "Document library independence"),
    ("fleet/modules/work/__init__.py", "import fleet.triage", "Module independence"),
    ("fleet/services/dispatch.py", "import argparse", "Services have no presentation dependencies"),
])
def test_forbidden_import_fails(tmp_path, filename, statement, contract):
    result = lint_copy(tmp_path, filename, statement)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{contract} BROKEN" in result.stdout, result.stdout
