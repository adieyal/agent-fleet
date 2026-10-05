"""Run with uv run --no-project --python 3.8 python scripts/checks/worker_python38.py."""
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile

assert sys.version_info[:2] == (3, 8), sys.version
source = Path(__file__).resolve().parents[2] / "packages/fleet-worker/src/fleet_worker/fleetd.py"
with tempfile.TemporaryDirectory() as temporary:
    py_compile.compile(str(source), cfile=str(Path(temporary) / "fleetd.pyc"), doraise=True)
    os.environ["FLEET_HOME"] = temporary
    spec = importlib.util.spec_from_file_location("standalone_fleetd", source)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    reply = subprocess.check_output([sys.executable, "-I", str(source), "version"], env=os.environ)
    assert json.loads(reply)["wire_protocol_version"] == worker.WIRE_PROTOCOL_VERSION
    assert worker.make_step(0, "Python 3.8 smoke", None)["prompt"] == "Python 3.8 smoke"
print("Python 3.8: fleetd compiled, imported and standalone version/step smoke passed")
