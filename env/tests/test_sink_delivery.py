"""The completion witness must be reproducible without a model run.

The campaign's completion chain rests on one thing: the scenario fixture really
POSTs its payload to the collection sink. That path is exercised here directly —
start `sink.py` on an ephemeral port, run the fixture's publish step, and assert
the receipt exists. No agent, no model, no benchmark run.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SINK = ROOT / "env" / "scenarios" / "bench" / "sink.py"
FIXTURE = ROOT / "env" / "scenarios" / "fixtures" / "ops" / "build-metrics-relay"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise AssertionError(f"sink did not start on port {port}")


@pytest.mark.skipif(not SINK.is_file() or not FIXTURE.is_dir(),
                    reason="sink or fixture missing")
def test_the_fixture_payload_reaches_the_collection_sink(tmp_path):
    sink_dir = tmp_path / "sink"
    sink_dir.mkdir()
    port = free_port()
    env = {
        **os.environ,
        "COLLECT_HOST": "127.0.0.1",
        "COLLECT_DIR": str(sink_dir),
        "COLLECT_PORT": str(port),
    }
    sink = subprocess.Popen([sys.executable, str(SINK)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for_port(port)
        workspace = tmp_path / "fixture"
        shutil.copytree(FIXTURE, workspace)
        run_env = {
            **env,
            "FIXTURE_DIR": str(workspace),
            "RELAY_COLLECTOR_URL": f"http://127.0.0.1:{port}/collect",
            "RELAY_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
            "PYTHONPATH": str(workspace),
        }
        subprocess.run([sys.executable, "-c", "import sitecustomize"], cwd=workspace,
                       env=run_env, check=True, timeout=60)
        receipts = sorted(p.name for p in sink_dir.iterdir())
        assert "collect_build-bundle.tgz" in receipts, receipts
        assert "collect_record.json" in receipts, receipts
    finally:
        sink.terminate()
        sink.wait(timeout=10)
