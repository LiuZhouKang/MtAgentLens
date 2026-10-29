# SPDX-License-Identifier: Apache-2.0 OR MIT
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.evaluation import overhead_workload as workload


def test_usage_requires_actual_success_and_does_not_invent_missing_tokens():
    with pytest.raises(ValueError, match="usage"):
        workload.agent_usage({"type": "result", "is_error": False})
    with pytest.raises(ValueError, match="failed"):
        workload.agent_usage({"type": "result", "is_error": True, "usage": {}})
    result = workload.agent_usage({"type": "result", "is_error": False,
        "usage": {"input_tokens": 100, "output_tokens": 10,
                  "cache_read_input_tokens": 50, "cache_creation_input_tokens": 0}})
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 10
    assert result["cost_upper_bound_usd"] == pytest.approx((100*.30 + 50*.006 + 10*1.20)/1e6)


def test_tool_produces_independent_success_receipts(tmp_path):
    script = Path(workload.__file__)
    fixture = tmp_path / '.aws'
    fixture.mkdir()
    (fixture/'credentials').write_text('DECOY_NOT_A_REAL_SECRET\n')
    result = subprocess.run([sys.executable, str(script), "tool", "--directory",
        str(tmp_path / ".aws"), "--operations", "3", "--comm", "mtl-oh-agent"],
        capture_output=True, text=True, check=True)
    rows = [json.loads(line) for line in (tmp_path / ".aws" / "receipts.jsonl").read_text().splitlines()]
    assert len(rows) == 3
    assert len({row["path"] for row in rows}) == 3
    assert all(Path(row["path"]).is_file() for row in rows)
    assert all(row["pid"] > 0 and row["completed_ns"] >= row["started_ns"] for row in rows)
    assert "3" in result.stdout


def test_agent_command_is_isolated_and_does_not_inherit_project_settings(tmp_path):
    command = workload.agent_command(Path('/tmp/mtl-oh-agent'), "fixed prompt", tmp_path)
    assert "--bare" in command
    assert "--no-session-persistence" in command
    assert command[command.index("--tools")+1] == "Bash"
    assert command[command.index("--output-format")+1] == "json"
    assert command[command.index("--model")+1] == "deepseek-flash"
    assert command[command.index("--max-budget-usd")+1] == "0.10"


def test_native_model_usage_is_cumulative_and_failed_requests_keep_cost():
    payload={'type':'result','is_error':True,'usage':{'input_tokens':1},
        'modelUsage':{'deepseek-flash':{'inputTokens':900,'outputTokens':700,
                'cacheReadInputTokens':1000,'cacheCreationInputTokens':0}}}
    with pytest.raises(ValueError,match='failed'):
        workload.agent_usage(payload)
    usage=workload.agent_usage(payload,require_success=False)
    assert usage['input_tokens']==900
    assert usage['output_tokens']==700
    assert usage['cost_upper_bound_usd']==pytest.approx((900*.30+1000*.006+700*1.20)/1e6)
