"""The collector command must carry the scenario id and mode it belongs to."""
from mtagentlens_benchmark.engine.collector_manager import _run_context_args


def test_run_context_args_bind_scenario_and_mode():
    assert _run_context_args("exfil-dns-tunnel", "single_agent") == [
        "--scenario-id",
        "exfil-dns-tunnel",
        "--run-mode",
        "single_agent",
    ]


def test_ad_hoc_runs_do_not_invent_context():
    assert _run_context_args(None, None) == []
    assert _run_context_args("", "") == []
