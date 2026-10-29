# SPDX-License-Identifier: Apache-2.0 OR MIT
"""A successful agent run with a dead probe must not count as a completed run."""

import json

from compare.scripts.run.pilot_observer import capture_sources_unavailable


def summary(tmp_path, configured, metrics):
    run = tmp_path / "run"
    run.mkdir()
    (run / "summary.json").write_text(json.dumps({
        "collector_health": {"runtime_metrics": {
            "configured_capture_sources": configured, **metrics}}}))
    return run


def test_disabled_process_probe_is_reported(tmp_path):
    run = summary(tmp_path, ["process", "tls"],
                  {"process_monitor_active": False, "tls_capture_active": True})
    assert capture_sources_unavailable(run) == ["process"]


def test_unavailable_tls_probe_is_reported(tmp_path):
    run = summary(tmp_path, ["tls"], {"tls_capture_active": False})
    assert capture_sources_unavailable(run) == ["tls"]


def test_healthy_or_legacy_summaries_are_not_flagged(tmp_path):
    healthy = summary(tmp_path, ["process", "tls"],
                      {"process_monitor_active": True, "tls_capture_active": True})
    assert capture_sources_unavailable(healthy) == []
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "summary.json").write_text(json.dumps({"collector_health": {}}))
    assert capture_sources_unavailable(legacy) == []
