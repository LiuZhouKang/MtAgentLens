"""Capture counters describe losses, never inferred success from missing logs."""
import ast
import json
import re
from pathlib import Path

import pytest

from mtagentlens_benchmark.engine.collector_manager import (
    _configured_capture_sources,
    _recover_configured_capture_sources,
)

SOURCE = Path(__file__).parents[1] / "src/mtagentlens_benchmark/engine/collector_manager.py"
namespace = {"Path": Path, "json": json, "re": re}
function = next(node for node in ast.parse(SOURCE.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == "_parse_runtime_metrics_from_logs")
exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
parse = namespace[function.name]


def test_cumulative_sources_are_not_summed_twice(tmp_path):
    log = '\n'.join(json.dumps(row) for row in [
        {"event": "RINGBUF_STATS", "dropped_events": 10},
        {"event": "RINGBUF_STATS", "dropped_events": 15},
        {"event": "TLS_RINGBUF_STATS", "dropped_events": 15},
        {"event": "PROCESS_RINGBUF_STATS", "dropped_events": 2, "rate_limit_drops": 7,
         "aggregation_table_full": 3},
    ])
    metrics = parse(log, tmp_path / "absent")
    assert metrics["ring_buffer_drops"] == 17
    assert metrics["rate_limit_drops"] == 7
    assert metrics["aggregation_table_full"] == 3
    assert metrics["capture_stats_complete"] is True


def test_missing_final_stats_are_unknown(tmp_path):
    metrics = parse('', tmp_path / "absent")
    assert metrics["ring_buffer_drops"] is None
    assert metrics["capture_stats_complete"] is False


def test_known_tls_source_needs_its_own_final_stats(tmp_path):
    log = '\n'.join(json.dumps(row) for row in [
        {"event": "TLS_CAPTURE_PROBES", "attached": 10},
        {"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0},
    ])
    assert parse(log, tmp_path / "absent")["capture_stats_complete"] is False


def test_capture_health_does_not_require_an_attack_and_rejects_loss(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"source":"ssl"}\n')
    log = json.dumps({"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0})
    metrics = parse(log, trace)
    assert metrics["overall_healthy"] is True
    assert metrics["pbc_detection_healthy"] is False
    lossy = json.dumps({"event": "PROCESS_RINGBUF_STATS", "dropped_events": 1})
    assert parse(lossy, trace)["overall_healthy"] is False


def test_stdio_final_counters_are_included_from_trace_events(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(json.dumps({"source": "stdio", "data": {
        "direction": "STATS", "dropped_events": 3,
    }}) + "\n")
    metrics = parse('{"event":"PROCESS_RINGBUF_STATS","dropped_events":0}', trace)
    assert metrics["ring_buffer_drops"] == 3
    assert metrics["capture_stats_complete"] is True
    assert metrics["overall_healthy"] is False


def test_configured_process_source_without_final_stats_is_incomplete(tmp_path):
    """A crashed or silent process helper must not look complete.

    expected_sources used to be discovered from the log, so a missing
    PROCESS_RINGBUF_STATS line removed the source from the expectation set and
    the run reported a complete, loss-free capture.
    """
    log = json.dumps({"event": "TLS_RINGBUF_STATS", "dropped_events": 0})
    metrics = parse(log, tmp_path / "absent", {"tls", "process"})
    assert metrics["capture_stats_complete"] is False
    assert metrics["overall_healthy"] is False


def test_configured_sources_that_all_report_final_stats_are_complete(tmp_path):
    log = '\n'.join(json.dumps(row) for row in [
        {"event": "TLS_RINGBUF_STATS", "dropped_events": 0},
        {"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0},
    ])
    metrics = parse(log, tmp_path / "absent", {"tls", "process"})
    assert metrics["capture_stats_complete"] is True


def test_configured_source_absent_from_the_log_stays_incomplete(tmp_path):
    log = json.dumps({"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0})
    assert parse(log, tmp_path / "absent", {"tls", "process"})["capture_stats_complete"] is False


def test_trace_args_determine_which_sources_must_report():
    source = Path(__file__).parents[1] / "src/mtagentlens_benchmark/engine/collector_manager.py"
    module = ast.parse(source.read_text())
    function = next(node for node in module.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "_configured_capture_sources")
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), scope)
    derive = scope[function.name]
    assert derive(["--ssl=true", "--process=true"]) == {"tls", "process"}
    assert derive(["--ssl=false", "--process=true"]) == {"process"}
    assert derive(["--stdio", "--pid", "42"]) == {"tls", "process", "stdio"}
    assert derive([]) == {"tls", "process"}


def test_process_loss_is_reported_by_cause(tmp_path):
    """A single total cannot say whether the ring buffer overflowed.

    The helper now emits the split; the parser must preserve it and keep the
    sum equal to dropped_events.
    """
    log = json.dumps({
        "event": "PROCESS_RINGBUF_STATS",
        "dropped_events": 9,
        "ringbuf_full_dropped": 2,
        "pending_table_dropped": 3,
        "user_read_dropped": 4,
    })
    metrics = parse(log, tmp_path / "absent")
    assert metrics["capture_loss_reasons"] == {
        "ringbuf_full": 2,
        "pending_table": 3,
        "user_read": 4,
    }
    assert sum(metrics["capture_loss_reasons"].values()) == metrics["ring_buffer_drops"]


def test_old_helpers_without_the_split_report_unknown_causes(tmp_path):
    log = json.dumps({"event": "PROCESS_RINGBUF_STATS", "dropped_events": 5})
    metrics = parse(log, tmp_path / "absent")
    assert metrics["capture_loss_reasons"] is None


def test_tls_and_process_reasons_are_summed_per_source(tmp_path):
    log = '\n'.join(json.dumps(row) for row in [
        {"event": "TLS_RINGBUF_STATS", "dropped_events": 1, "ringbuf_full_dropped": 1},
        {"event": "PROCESS_RINGBUF_STATS", "dropped_events": 2, "user_read_dropped": 2},
    ])
    metrics = parse(log, tmp_path / "absent")
    assert metrics["capture_loss_reasons"] == {
        "ringbuf_full": 1,
        "pending_table": 0,
        "user_read": 2,
    }


def test_final_stats_missing_required_dropped_events_is_not_loss_free(tmp_path):
    """E01: a final stats row without dropped_events is not a zero counter.

    The parser used to default the missing field to 0, so a malformed final
    counter produced complete, loss-free, healthy stats.
    """
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"source":"ssl"}\n')
    metrics = parse('{"event":"TLS_RINGBUF_STATS"}', trace, {"tls"})
    assert metrics["ring_buffer_drops"] is None
    assert metrics["capture_stats_complete"] is False
    assert metrics["capture_loss_free"] is False
    assert metrics["overall_healthy"] is False


def test_invalid_final_dropped_events_values_are_not_counters(tmp_path):
    """Booleans, negatives, strings, floats and null are invalid counts."""
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"source":"ssl"}\n')
    for value in (True, False, -1, "0", 1.5, None):
        log = json.dumps({"event": "PROCESS_RINGBUF_STATS", "dropped_events": value})
        metrics = parse(log, trace)
        assert metrics["ring_buffer_drops"] is None, value
        assert metrics["capture_stats_complete"] is False, value
        assert metrics["capture_loss_free"] is False, value
        assert metrics["overall_healthy"] is False, value


def test_invalid_optional_legacy_counters_invalidate_final_stats(tmp_path):
    """Present optional counters must also be non-negative integers."""
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"source":"ssl"}\n')
    for extra in (
        {"rate_limit_drops": True},
        {"aggregation_table_full": -1},
        {"ringbuf_full_dropped": "1"},
        {"user_read_dropped": 2.5},
    ):
        log = json.dumps({"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0, **extra})
        metrics = parse(log, trace)
        assert metrics["capture_stats_complete"] is False, extra
        assert metrics["capture_loss_free"] is False, extra
        assert metrics["overall_healthy"] is False, extra


def test_one_malformed_final_counter_invalidates_the_run(tmp_path):
    """A valid sibling source cannot paper over an unknown source."""
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"source":"ssl"}\n')
    log = '\n'.join(json.dumps(row) for row in [
        {"event": "TLS_RINGBUF_STATS"},
        {"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0},
    ])
    metrics = parse(log, trace, {"tls", "process"})
    assert metrics["capture_stats_complete"] is False
    assert metrics["capture_loss_free"] is False
    assert metrics["overall_healthy"] is False


def test_valid_final_counters_with_optional_fields_absent_still_pass(tmp_path):
    """Legacy helpers without the loss split remain supported."""
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"source":"ssl"}\n')
    log = json.dumps({"event": "PROCESS_RINGBUF_STATS", "dropped_events": 0})
    metrics = parse(log, trace)
    assert metrics["ring_buffer_drops"] == 0
    assert metrics["capture_loss_reasons"] is None
    assert metrics["capture_stats_complete"] is True
    assert metrics["capture_loss_free"] is True
    assert metrics["overall_healthy"] is True


def test_malformed_latest_final_invalidates_an_earlier_valid_final(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"source":"ssl"}\n')
    for source, marker in [
        ("tls", {"event": "TLS_RINGBUF_STATS"}),
        ("process", {"event": "PROCESS_RINGBUF_STATS"}),
        ("stdio", {"direction": "STATS"}),
    ]:
        log = '\n'.join(json.dumps(row) for row in [
            {**marker, "dropped_events": 0}, marker,
        ])
        metrics = parse(log, trace, {source})
        assert metrics["ring_buffer_drops"] is None, source
        assert metrics["capture_stats_complete"] is False, source
        assert metrics["capture_loss_free"] is False, source
        assert metrics["overall_healthy"] is False, source


def test_non_object_json_does_not_crash_capture_quality_parsing(tmp_path):
    metrics = parse('[]\nnull\n42', tmp_path / 'absent', {"tls"})
    assert metrics["capture_stats_complete"] is False
    assert metrics["ring_buffer_drops"] is None


def test_health_snapshot_recovers_the_configured_sources():
    assert _recover_configured_capture_sources(
        {"configured_capture_sources": ["tls", "process"]}
    ) == {"tls", "process"}
    assert _recover_configured_capture_sources(
        {"runtime_metrics": {"configured_capture_sources": ["stdio"]}}
    ) == {"stdio"}


def test_health_snapshot_trace_args_use_the_same_source_semantics():
    trace_args = ["--ssl=false", "--process=true", "--stdio"]
    assert _recover_configured_capture_sources({"trace_args": trace_args}) == (
        _configured_capture_sources(trace_args)
    ) == {"process", "stdio"}


def test_absent_health_source_config_is_unknown_not_inferred():
    assert _recover_configured_capture_sources({}) is None
    assert _recover_configured_capture_sources({"runtime_metrics": {}}) is None


def test_source_defaults_and_split_boolean_values_match_the_trace_cli():
    assert _configured_capture_sources([]) == {"tls", "process"}
    assert _configured_capture_sources(["--stdio", "--pid", "42"]) == {
        "tls", "process", "stdio",
    }
    assert _configured_capture_sources(["--ssl", "false", "--process", "true"]) == {"process"}
    assert _configured_capture_sources(["--", "--ssl=false"]) == {"tls", "process"}


def test_conflicting_persisted_source_configs_are_rejected():
    with pytest.raises(ValueError, match="conflict"):
        _recover_configured_capture_sources({
            "configured_capture_sources": ["tls"],
            "trace_args": ["--ssl=false", "--process=true"],
        })


@pytest.mark.parametrize("health", [
    {"configured_capture_sources": "tls"},
    {"configured_capture_sources": ["tls", "bogus"]},
    {"trace_args": "--ssl=true"},
    {"runtime_metrics": {"trace_args": [1, 2]}},
])
def test_malformed_health_source_config_fails_closed(health):
    with pytest.raises(ValueError):
        _recover_configured_capture_sources(health)


def test_process_action_reservation_losses_cannot_be_excluded_as_outside_reads():
    """A full action ring has no failure notice and must remain a global loss."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "bpftools/process/process.bpf.c").read_text()
    failures = re.findall(
        r"bpf_ringbuf_reserve\([^;]+;\s*if\s*\(!e\)\s*\{([^}]+)\}", source
    )
    assert failures, "No action reservation failure paths found"
    assert all("inc_dropped(DROP_RINGBUF_FULL)" in body for body in failures), failures
