"""Contract tests for the two-step judge adapter, cache, resume, and honesty gates.

Every test is offline: the judge talks to an injected fake client, and the
default client is a loud ``UnavailableClient``. Nothing here can produce a
detection number, by construction -- the judge never sees ground truth.
"""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest

from compare.rjudge.cache import CACHE_SCHEMA, ResponseCache
from compare.rjudge.judge import (
    AnthropicMessagesClient,
    ExecutionMode,
    JudgeClientUnavailable,
    JudgeConfig,
    JudgeRunner,
    RJudgeJudge,
    UnavailableClient,
    RESULT_SCHEMA,
    SUMMARY_SCHEMA,
)
from compare.rjudge.protocol import LABEL_PROMPT, REASON_PROMPT, LabelParseMode, prompt_bundle_sha256
from compare.rjudge.record import RJudgeRecord, record_sha256


# ── fixtures ──────────────────────────────────────────────────────────────

def make_record(
    trace_id: str = "trace-1",
    *,
    profile: str = "You are an agent.",
    contents: list | None = None,
    trace_complete: bool = True,
) -> RJudgeRecord:
    return RJudgeRecord(
        profile=profile,
        contents=contents
        or [[{"role": "user", "content": "Run make test."},
             {"role": "agent", "thought": "I should run it.", "action": "Bash: make test"}]],
        trace_id=trace_id,
        trace_sha256=hashlib.sha256(trace_id.encode("utf-8")).hexdigest(),
        trace_complete=trace_complete,
        profile_source="test",
        agent_ids=("agent_0",),
        arm="A2_full_trace",
    )


class FakeClient:
    client_id = "fake-client"

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, *, model, messages, temperature, max_tokens, seed=None):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "seed": seed,
            }
        )
        index = len(self.calls) - 1
        return self.responses[index] if index < len(self.responses) else self.responses[-1]


METRIC_TOKENS = ("tpr", "fpr", "accuracy", "precision", "recall", "auc", "mcc")


def _assert_no_metric_numbers(summary: dict) -> None:
    """No detection metric may be exposed as a numeric field by this package."""
    for key in summary:
        assert not any(token in key.lower() for token in METRIC_TOKENS), key
    assert summary["metrics"] is None
    assert summary["metrics_status"] == "deferred_to_evaluator"


def make_judge(
    client=None,
    *,
    cache: ResponseCache | None = None,
    execution_mode: ExecutionMode = ExecutionMode.MOCK,
    model: str = "deepseek-v4-flash",
    label_parse_mode: LabelParseMode | str = LabelParseMode.STRICT,
) -> RJudgeJudge:
    return RJudgeJudge(
        client=client,
        config=JudgeConfig(model=model, label_parse_mode=label_parse_mode),
        cache=cache,
        execution_mode=execution_mode,
    )


# ── 1. Protocol shape of the two model calls ──────────────────────────────

def test_judge_performs_two_step_protocol_in_order() -> None:
    client = FakeClient("The agent ran a test suite; nothing dangerous.", "safe")

    verdict = make_judge(client).judge(make_record())

    assert len(client.calls) == 2

    first = client.calls[0]
    assert first["messages"][-1] == {"role": "user", "content": REASON_PROMPT}
    assert first["max_tokens"] == 500
    assert first["temperature"] == 0.0

    second = client.calls[1]
    assert second["messages"][-2] == {
        "role": "assistant",
        "content": "The agent ran a test suite; nothing dangerous.",
    }
    assert second["messages"][-1] == {"role": "user", "content": LABEL_PROMPT}
    assert second["messages"][: len(first["messages"])] == first["messages"]
    assert second["max_tokens"] == 500

    assert verdict.status == "ok"
    assert verdict.label == "safe"
    assert verdict.reason == "The agent ran a test suite; nothing dangerous."
    assert verdict.label_raw == "safe"
    assert verdict.prompt_sha256 == prompt_bundle_sha256()


def test_judge_forwards_configured_seed_and_temperature() -> None:
    client = FakeClient("analysis", "unsafe")
    judge = RJudgeJudge(
        client=client,
        config=JudgeConfig(model="m", temperature=0.0, seed=1234),
        execution_mode=ExecutionMode.REAL,
    )

    judge.judge(make_record())

    assert [call["seed"] for call in client.calls] == [1234, 1234]
    assert [call["temperature"] for call in client.calls] == [0.0, 0.0]


def test_judge_reproduces_official_gpt_label_token_quirk() -> None:
    fake_gpt = FakeClient("analysis", "unsafe")
    make_judge(fake_gpt, model="gpt-4o-2024-08-06").judge(make_record())
    assert fake_gpt.calls[1]["max_tokens"] == 1

    fake_other = FakeClient("analysis", "unsafe")
    make_judge(fake_other, model="deepseek-v4-flash").judge(make_record())
    assert fake_other.calls[1]["max_tokens"] == 500


def test_label_max_tokens_can_be_overridden_explicitly() -> None:
    client = FakeClient("analysis", "safe")
    judge = RJudgeJudge(
        client=client,
        config=JudgeConfig(model="gpt-4o", max_tokens_label=16),
        execution_mode=ExecutionMode.MOCK,
    )

    judge.judge(make_record())

    assert client.calls[1]["max_tokens"] == 16


def test_judge_returns_unsafe_label() -> None:
    verdict = make_judge(FakeClient("exfiltration detected", "unsafe")).judge(make_record())

    assert verdict.label == "unsafe"
    assert verdict.status == "ok"
    assert verdict.parse_error is None


# ── 2. Strict parsing: ambiguity is a failure, never a guess ──────────────

@pytest.mark.parametrize(
    "bad_response",
    ["", "I cannot decide.", "safe and unsafe", "The actions are safe."],
)
def test_judge_parse_error_is_a_strict_failure(bad_response: str) -> None:
    client = FakeClient("analysis", bad_response)

    verdict = make_judge(client).judge(make_record())

    assert verdict.status == "parse_error"
    assert verdict.label is None
    assert verdict.parse_error is not None
    assert verdict.parse_error["code"]
    assert verdict.parse_error["response"] == bad_response
    assert verdict.label_raw == bad_response


def test_protocol_tokens_mode_is_opt_in_and_reported() -> None:
    strict = make_judge(FakeClient("analysis", "safe.")).judge(make_record())
    assert strict.status == "parse_error"

    tolerant = make_judge(
        FakeClient("analysis", "safe."), label_parse_mode=LabelParseMode.PROTOCOL_TOKENS
    ).judge(make_record())
    assert tolerant.status == "ok"
    assert tolerant.label == "safe"


# ── 3. Cache: content-addressed, persistent, and honest about hits ────────

def test_cache_prevents_a_second_model_call(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "cache")
    client = FakeClient("analysis", "safe")
    judge = make_judge(client, cache=cache)

    first = judge.judge(make_record())
    second = judge.judge(make_record())

    assert len(client.calls) == 2  # one two-step run only
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.label == first.label


def test_cache_key_depends_on_model_temperature_trace_and_prompt(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "cache")
    client = FakeClient("analysis", "safe")
    judge = make_judge(client, cache=cache)

    judge.judge(make_record("trace-a"))
    before = len(client.calls)

    judge.judge(make_record("trace-b"))  # different trace hash -> new call
    assert len(client.calls) == before + 2

    other_model = make_judge(FakeClient("analysis", "safe"), cache=cache, model="another-model")
    other_model.judge(make_record("trace-a"))
    assert len(other_model.client.calls) == 2

    # Changing only temperature must also miss the cache.
    warmer = RJudgeJudge(
        client=FakeClient("analysis", "safe"),
        config=JudgeConfig(model="deepseek-v4-flash", temperature=0.7),
        cache=cache,
        execution_mode=ExecutionMode.MOCK,
    )
    verdict = warmer.judge(make_record("trace-a"))
    assert verdict.from_cache is False
    assert len(warmer.client.calls) == 2


def test_cache_persists_across_instances(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    first = make_judge(FakeClient("analysis", "safe"), cache=ResponseCache(cache_dir))
    first.judge(make_record())

    second_client = FakeClient("analysis", "safe")
    second = make_judge(second_client, cache=ResponseCache(cache_dir))
    verdict = second.judge(make_record())

    assert verdict.from_cache is True
    assert second_client.calls == []


def test_cache_entries_are_auditable_json(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "cache")
    make_judge(FakeClient("analysis", "safe"), cache=cache).judge(make_record())

    files = sorted((tmp_path / "cache").rglob("*.json"))
    assert len(files) == 2  # reason step + label step
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema"] == CACHE_SCHEMA
        assert payload["response"]
        assert payload["meta"]["trace_sha256"] == hashlib.sha256(b"trace-1").hexdigest()
        assert payload["meta"]["model"] == "deepseek-v4-flash"
        assert payload["meta"]["prompt_sha256"] == prompt_bundle_sha256()
        assert payload["meta"]["step"] in {"reason", "label"}


def test_corrupt_cache_entry_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    client = FakeClient("analysis", "safe")
    judge = make_judge(client, cache=ResponseCache(cache_dir))
    judge.judge(make_record())

    for path in cache_dir.rglob("*.json"):
        path.write_text("{not json", encoding="utf-8")

    client.calls.clear()
    verdict = make_judge(client, cache=ResponseCache(cache_dir)).judge(make_record())

    assert verdict.from_cache is False
    assert len(client.calls) == 2


def test_cache_disabled_still_judges(tmp_path: Path) -> None:
    client = FakeClient("analysis", "safe")
    judge = make_judge(client, cache=None)

    judge.judge(make_record())
    judge.judge(make_record())

    assert len(client.calls) == 4
    assert not list(tmp_path.rglob("*.json"))


# ── 4. No network by default ──────────────────────────────────────────────

def test_default_client_is_unavailable_and_refuses_to_call() -> None:
    judge = RJudgeJudge(config=JudgeConfig(model="m"))

    assert isinstance(judge.client, UnavailableClient)
    with pytest.raises(JudgeClientUnavailable):
        judge.judge(make_record())


def test_judge_with_fake_client_never_opens_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args, **kwargs):  # pragma: no cover - only on regression
        raise AssertionError("judging must not touch the network")

    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(socket.socket, "connect", _forbidden)

    verdict = make_judge(FakeClient("analysis", "safe")).judge(make_record())

    assert verdict.label == "safe"


def test_anthropic_client_economical_default_and_explicit_override(monkeypatch) -> None:
    monkeypatch.setattr("compare.rjudge.judge._load_api_config", lambda: {})
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    client = AnthropicMessagesClient(auth_token="")
    assert client.default_model == "deepseek-flash"
    monkeypatch.setenv("ANTHROPIC_MODEL", "configured-model")
    assert AnthropicMessagesClient(auth_token="").default_model == "configured-model"
    assert AnthropicMessagesClient(auth_token="", model="explicit-model").default_model == "explicit-model"


def test_anthropic_client_without_credentials_refuses_to_call() -> None:
    client = AnthropicMessagesClient(base_url="http://127.0.0.1:9", auth_token="", model="m")

    assert client.available is False
    with pytest.raises(JudgeClientUnavailable):
        client.complete(model="m", messages=[{"role": "user", "content": "x"}], temperature=0.0, max_tokens=1)


# ── 5. Runner: JSONL results, resume, and the honesty gate ────────────────

def test_runner_writes_one_jsonl_line_per_record(tmp_path: Path) -> None:
    out = tmp_path / "results" / "rjudge.jsonl"
    runner = JudgeRunner(
        judge=make_judge(FakeClient("analysis", "safe", "analysis", "unsafe")),
        out_jsonl=out,
    )

    summary = runner.run([make_record("trace-a"), make_record("trace-b")])

    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert summary["schema"] == SUMMARY_SCHEMA
    assert summary["n_records"] == 2
    assert summary["n_ok"] == 2
    assert summary["n_resumed"] == 0

    for line in lines:
        assert line["schema"] == RESULT_SCHEMA
        assert set(line) >= {
            "schema",
            "trace_id",
            "record_sha256",
            "trace_sha256",
            "model",
            "prompt_sha256",
            "executed",
            "execution_mode",
            "status",
            "label",
            "parse_error",
            "reason",
            "label_raw",
            "from_cache",
            "created_at",
        }
        assert line["record_sha256"] == record_sha256(make_record(line["trace_id"]))
        assert line["prompt_sha256"] == prompt_bundle_sha256()
    assert {line["trace_id"] for line in lines} == {"trace-a", "trace-b"}


def test_runner_resume_skips_completed_records(tmp_path: Path) -> None:
    out = tmp_path / "rjudge.jsonl"
    first_client = FakeClient("analysis", "safe")
    JudgeRunner(judge=make_judge(first_client), out_jsonl=out).run([make_record("trace-a")])
    assert len(first_client.calls) == 2

    second_client = FakeClient("analysis", "unsafe")
    summary = JudgeRunner(
        judge=make_judge(second_client), out_jsonl=out, resume=True
    ).run([make_record("trace-a"), make_record("trace-b")])

    assert summary["n_resumed"] == 1
    assert summary["n_ok"] == 2  # one resumed + one newly judged result in the file
    assert len(second_client.calls) == 2
    assert len(out.read_text(encoding="utf-8").splitlines()) == 2


def test_runner_with_resume_false_rejudges(tmp_path: Path) -> None:
    out = tmp_path / "rjudge.jsonl"
    JudgeRunner(judge=make_judge(FakeClient("a", "safe")), out_jsonl=out).run([make_record("trace-a")])

    client = FakeClient("a", "safe")
    summary = JudgeRunner(judge=make_judge(client), out_jsonl=out, resume=False).run(
        [make_record("trace-a")]
    )

    assert summary["n_resumed"] == 0
    assert len(client.calls) == 2
    assert len(out.read_text(encoding="utf-8").splitlines()) == 2


def test_runner_reports_parse_errors_without_inventing_labels(tmp_path: Path) -> None:
    out = tmp_path / "rjudge.jsonl"
    runner = JudgeRunner(judge=make_judge(FakeClient("analysis", "not sure")), out_jsonl=out)

    summary = runner.run([make_record("trace-a")])

    line = json.loads(out.read_text(encoding="utf-8").strip())
    assert line["status"] == "parse_error"
    assert line["label"] is None
    assert summary["n_parse_error"] == 1
    assert summary["n_ok"] == 0


def test_runner_marks_incomplete_traces_and_never_calls_the_model(tmp_path: Path) -> None:
    out = tmp_path / "rjudge.jsonl"
    client = FakeClient("analysis", "safe")
    runner = JudgeRunner(judge=make_judge(client), out_jsonl=out)

    summary = runner.run([make_record("trace-a", trace_complete=False)])

    assert summary["n_trace_incomplete"] == 1
    assert client.calls == []
    line = json.loads(out.read_text(encoding="utf-8").strip())
    assert line["status"] == "trace_incomplete"
    assert line["label"] is None


def test_mock_execution_is_marked_not_executed_and_produces_no_numbers(tmp_path: Path) -> None:
    summary = JudgeRunner(
        judge=make_judge(FakeClient("analysis", "safe")), out_jsonl=tmp_path / "rjudge.jsonl"
    ).run([make_record("trace-a")])

    assert summary["executed"] is False
    assert summary["execution_mode"] == "mock"
    assert summary["metrics"] is None
    assert summary["parse_error_rate"] is None
    assert "no detection metric" in " ".join(summary["notes"]).lower()

    _assert_no_metric_numbers(summary)


def test_real_execution_summary_still_has_no_detection_metrics(tmp_path: Path) -> None:
    summary = JudgeRunner(
        judge=make_judge(FakeClient("analysis", "safe"), execution_mode=ExecutionMode.REAL),
        out_jsonl=tmp_path / "rjudge.jsonl",
    ).run([make_record("trace-a")])

    assert summary["executed"] is True
    assert summary["execution_mode"] == "real"
    assert summary["metrics"] is None
    assert summary["metrics_status"] == "deferred_to_evaluator"
    assert summary["parse_error_rate"] == 0.0
    _assert_no_metric_numbers(summary)


def test_runner_writes_summary_file_when_requested(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    runner = JudgeRunner(
        judge=make_judge(FakeClient("analysis", "safe")),
        out_jsonl=tmp_path / "rjudge.jsonl",
        summary_path=summary_path,
    )

    returned = runner.run([make_record("trace-a")])

    assert json.loads(summary_path.read_text(encoding="utf-8")) == returned


# ── 6. Verdict serialisation ──────────────────────────────────────────────

def test_verdict_json_is_stable_and_auditable() -> None:
    verdict = make_judge(FakeClient("analysis", "safe")).judge(make_record())

    payload = verdict.to_json()
    assert payload["trace_id"] == "trace-1"
    assert payload["executed"] is False
    assert payload["execution_mode"] == "mock"
    assert payload["status"] == "ok"
    assert payload["label"] == "safe"
    assert json.loads(json.dumps(payload)) == payload


def test_incomplete_trace_is_never_marked_executed_even_in_real_mode(tmp_path: Path) -> None:
    out = tmp_path / "rjudge.jsonl"
    runner = JudgeRunner(
        judge=make_judge(FakeClient("a", "safe"), execution_mode=ExecutionMode.REAL),
        out_jsonl=out,
    )

    summary = runner.run([make_record("trace-a", trace_complete=False)])

    line = json.loads(out.read_text(encoding="utf-8").strip())
    assert line["status"] == "trace_incomplete"
    assert line["executed"] is False
    assert summary["executed"] is False
    assert summary["n_judged"] == 0


def test_mock_smoke_results_cannot_satisfy_a_real_run_resume(tmp_path: Path) -> None:
    out = tmp_path / "rjudge.jsonl"
    # Smoke run first (mock client, mock mode).
    JudgeRunner(
        judge=make_judge(FakeClient("a", "safe"), execution_mode=ExecutionMode.MOCK),
        out_jsonl=out,
    ).run([make_record("trace-a")])

    real_client = FakeClient("analysis", "unsafe")
    summary = JudgeRunner(
        judge=make_judge(real_client, execution_mode=ExecutionMode.REAL),
        out_jsonl=out,
        resume=True,
    ).run([make_record("trace-a")])

    assert summary["n_resumed"] == 0          # mock result was NOT reused
    assert len(real_client.calls) == 2        # the real run actually judged
    assert summary["executed"] is True


def test_empty_first_stage_stops_before_spending_on_label():
    client=FakeClient('', 'safe')
    client.last_response_metadata={'stop_reason':'max_tokens','usage':{'output_tokens':500}}
    judge=RJudgeJudge(config=JudgeConfig(model='fake'),client=client,execution_mode=ExecutionMode.MOCK)
    result=judge.judge(make_record())
    assert result.status=='error'
    assert result.label is None
    assert len(client.calls)==1
    assert result.to_json()['request_metadata']['reason']['stop_reason']=='max_tokens'


def test_live_client_exposes_only_usage_and_stop_metadata_without_network(monkeypatch):
    import urllib.request
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'content':[],'stop_reason':'max_tokens','usage':{'input_tokens':100,'output_tokens':500,'unrelated':'ignored'}}).encode()
    monkeypatch.setattr(urllib.request,'urlopen',lambda *args,**kwargs:Response())
    client=AnthropicMessagesClient(base_url='http://127.0.0.1',auth_token='test-only',model='fake')
    assert client.complete(model='fake',messages=[{'role':'user','content':'short review'}],temperature=0.0,max_tokens=500)==''
    assert client.last_response_metadata=={'stop_reason':'max_tokens','usage':{'input_tokens':100,'output_tokens':500}}


def test_resume_does_not_accept_old_ok_result_with_empty_reason(tmp_path):
    record=make_record()
    first=RJudgeJudge(config=JudgeConfig(model='fake'),client=FakeClient('old analysis','safe'),execution_mode=ExecutionMode.MOCK)
    old=first.judge(record).to_json();old['reason']=''
    path=tmp_path/'verdicts.jsonl';path.write_text(json.dumps(old)+'\n')
    client=FakeClient('complete short review','safe')
    judge=RJudgeJudge(config=JudgeConfig(model='fake'),client=client,execution_mode=ExecutionMode.MOCK)
    summary=JudgeRunner(judge=judge,out_jsonl=path).run([record])
    assert summary['n_resumed']==0 and len(client.calls)==2


@pytest.mark.parametrize('markup', ['<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="Bash">pwd</｜｜DSML｜｜ invoke>', '<|DSML|calls>pwd</|DSML|calls>'])
def test_first_stage_tool_invocation_stops_before_label(markup):
    client = FakeClient(markup, 'safe')
    result = make_judge(client).judge(make_record())
    assert result.status == 'error'
    assert result.label is None and result.reason == markup
    assert len(client.calls) == 1
    assert 'tool invocation' in result.error


def test_resume_does_not_accept_old_ok_result_with_tool_invocation(tmp_path):
    record = make_record()
    old = make_judge(FakeClient('analysis', 'safe')).judge(record).to_json()
    old['reason'] = '<｜｜DSML｜｜ calls>pwd</｜｜DSML｜｜ calls>'
    path = tmp_path / 'verdicts.jsonl'
    path.write_text(json.dumps(old) + '\n')
    client = FakeClient('The recorded test command is safe.', 'safe')
    summary = JudgeRunner(judge=make_judge(client), out_jsonl=path).run([record])
    assert summary['n_resumed'] == 0 and len(client.calls) == 2


def test_analysis_quoting_tool_markup_is_not_itself_a_tool_invocation():
    client = FakeClient('The agent emitted <｜｜DSML｜｜ calls> to run a harmless test.', 'safe')
    assert make_judge(client).judge(make_record()).status == 'ok'
