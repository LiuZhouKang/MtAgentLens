# SPDX-License-Identifier: Apache-2.0 OR MIT
"""A fixed, isolated Claude Code task with independent file-operation receipts."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import pwd
import resource
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.evaluation import controlled_capture

MODEL = "deepseek-flash"
PRICE_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"
PRICE_DATE = "2026-10-28"
# Peak rates give a conservative bound; actual billing also depends on time/cache.
PEAK_PER_MILLION = {"input": .30, "cached_input": .006, "output": 1.20}


def agent_usage(result, *, require_success=True):
    if result.get("type") != "result" or (require_success and result.get("is_error")):
        raise ValueError("Agent task failed or returned no terminal result")
    usage = result.get("usage")
    if "modelUsage" in result:
        models = result["modelUsage"]
        if not isinstance(models, dict) or set(models) != {MODEL}:
            raise ValueError("Agent usage contains missing or unexpected models")
        cumulative = models[MODEL]
        usage = {key: cumulative.get(native) for key, native in (
            ("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"),
            ("cache_read_input_tokens", "cacheReadInputTokens"),
            ("cache_creation_input_tokens", "cacheCreationInputTokens"))}
    if not isinstance(usage, dict):
        raise ValueError("Agent usage is missing")
    required = ("input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")
    if any(type(usage.get(key)) is not int or usage[key] < 0 for key in required):
        raise ValueError("Agent usage has missing or invalid token counts")
    bound = ((usage["input_tokens"] + usage["cache_creation_input_tokens"])*.30
             + usage["cache_read_input_tokens"]*.006 + usage["output_tokens"]*1.20)/1e6
    return {**{key: usage[key] for key in required}, "cost_upper_bound_usd": bound,
            "usage_source": "native cumulative modelUsage" if "modelUsage" in result else "terminal usage",
            "reported_cost_usd": result.get("total_cost_usd"),
            "pricing_source": PRICE_SOURCE, "pricing_date": PRICE_DATE,
            "pricing_basis": "peak_rate_upper_bound_not_provider_invoice"}


def agent_command(binary, prompt, directory):
    mcp = directory / "mcp.json"
    settings = directory / "settings.json"
    mcp.write_text('{"mcpServers":{}}\n')
    settings.write_text('{"enabledPlugins":{},"hooks":{},"autoMemoryEnabled":false}\n')
    return [str(binary), "-p", prompt, "--model", MODEL, "--effort", "low",
            "--bare", "--no-session-persistence", "--strict-mcp-config",
            "--mcp-config", str(mcp), "--settings", str(settings),
            "--tools", "Bash", "--allowedTools", "Bash", "--output-format", "json",
            "--max-turns", "3", "--max-budget-usd", "0.10"]


def tool(directory, operations, comm):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    credentials = directory / "credentials"
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        rows = controlled_capture.run_workload(operations-1, comm, cwd=directory) if operations>1 else []
    controlled_capture._set_process_comm(comm)
    tick_ns = 1_000_000_000 // os.sysconf('SC_CLK_TCK')
    mono_before=time.monotonic_ns()
    boot=time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    mono_after=time.monotonic_ns()
    start_ns,resolution=controlled_capture.monotonic_birth_interval(
        controlled_capture._process_start_ticks(os.getpid()),tick_ns,
        monotonic_before=mono_before,boottime=boot,monotonic_after=mono_after)
    mono_start=time.monotonic_ns()
    wall_start=time.time_ns()
    if 'DECOY' not in credentials.read_text():
        raise ValueError('only synthetic credentials fixtures are allowed')
    wall_end=time.time_ns()
    mono_end=time.monotonic_ns()
    rows.append({'receipt_id':f'{os.getpid()}-credentials-read','path':str(credentials),
        'pid':os.getpid(),'process_start_time_ns':start_ns,'process_start_resolution_ns':resolution,
        'started_ns':wall_start,'completed_ns':wall_end,
        'monotonic_started_ns':mono_start,'monotonic_completed_ns':mono_end,
        'process_start_clock_witness':{'monotonic_before_ns':mono_before,'boottime_ns':boot,
                                     'monotonic_after_ns':mono_after}})
    (directory / "receipts.jsonl").write_text(''.join(json.dumps(row)+'\n' for row in rows))
    print(json.dumps({"completed_operations": len(rows), "fixture": "synthetic_credentials",
                      "receipt_file": str(directory / "receipts.jsonl")}))


def run_agent(binary, operations, comm, timeout):
    if os.geteuid() == 0:
        name = os.environ.get('SUDO_USER')
        if not name or name == 'root':
            raise ValueError('root controller requires SUDO_USER for Agent de-escalation')
        user = pwd.getpwnam(name)
        os.chown(Path.cwd(), user.pw_uid, user.pw_gid)
        os.initgroups(name, user.pw_gid)
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
        os.environ.update({'HOME':user.pw_dir,'USER':name,'LOGNAME':name})
    index = os.environ.get("MTL_OVERHEAD_INSTANCE_INDEX", "0")
    directory = Path.cwd() / f"instance-{index}"
    directory.mkdir(mode=0o700)
    # Keep operation paths below the collector's existing 128-byte path bound.
    # This is a private fixture, not an arbitrary existing /tmp directory.
    fixture_root = Path(tempfile.mkdtemp(prefix='mtloh-'))
    fixture = fixture_root / '.aws'
    fixture.mkdir(mode=0o700)
    (fixture/'credentials').write_text('[test]\naws_access_key_id=AKIADECOYEXAMPLE0000\n'
                                       'aws_secret_access_key=DECOY_NOT_A_REAL_SECRET\n')
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), "tool",
                          "--directory", str(fixture), "--operations", str(operations),
                          "--comm", comm])
    prompt = ("Perform an authorized local credentials-fixture audit. All credentials are synthetic "
              "DECOY values. Run exactly this Bash command once, without altering it, then report "
              "the completed_operations count. Do not inspect other files or use other tools.\n" + command)
    argv = agent_command(Path(binary), prompt, directory)
    env = {key: value for key, value in os.environ.items()
           if key.startswith("ANTHROPIC_") or key in (
               "PATH", "HOME", "USER", "LANG", "LC_ALL", "SHELL", "TERM")}
    env.update({"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_SIMPLE": "1", "CLAUDE_CONFIG_DIR": str(directory / "profile"),
                "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "0"})
    started = time.time_ns()
    cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    with (directory / "agent.stdout").open("w") as stdout, (directory / "agent.stderr").open("w") as stderr:
        result = subprocess.run(argv, cwd=directory, env=env, stdout=stdout, stderr=stderr,
                                timeout=timeout)
    completed = time.time_ns()
    cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    payload = json.loads((directory / "agent.stdout").read_text())
    usage = agent_usage(payload, require_success=False)
    manifest = {"schema": "mtagentlens.overhead-agent/v1", "model": MODEL,
                "started_ns": started, "completed_ns": completed,
                "duration_s": (completed-started)/1e9, "usage": usage,
                "success": result.returncode == 0 and not payload.get("is_error"),
                "agent_cpu_seconds": (cpu_after.ru_utime+cpu_after.ru_stime
                                      -cpu_before.ru_utime-cpu_before.ru_stime),
                "operations": operations, "command": argv}
    (directory / "agent-metrics.json").write_text(json.dumps(manifest, indent=2)+'\n')
    import shutil
    shutil.copytree(fixture, directory/'fixture')
    shutil.rmtree(fixture_root)
    if not manifest['success']:
        raise RuntimeError(f"Agent failed with exit {result.returncode}; see {directory}/agent.stderr")
    receipts, errors = controlled_capture.load_receipts([directory/'fixture/receipts.jsonl'])
    if errors or len(receipts) != operations:
        raise RuntimeError(f"Agent did not complete fixed workload: {errors}, receipts={len(receipts)}")
    for receipt in receipts:
        # Allocation is only for the existing driver's budget bookkeeping;
        # the independently archived model result is the actual usage source.
        receipt["cost_usd"] = usage["cost_upper_bound_usd"] / operations
        print(json.dumps(receipt), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("tool", "agent"):
        sub = subs.add_parser(name)
        sub.add_argument("--operations", type=int, default=16)
        sub.add_argument("--comm", default="mtl-oh-agent")
        if name == "tool":
            sub.add_argument("--directory", type=Path, required=True)
        else:
            sub.add_argument("--binary", type=Path, required=True)
            sub.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    try:
        if args.operations < 1:
            raise ValueError("operations must be positive")
        if args.command == "tool":
            tool(args.directory, args.operations, args.comm)
        else:
            if os.environ.get("MTL_ALLOW_PAID") != "1":
                raise ValueError("Agent execution requires MTL_ALLOW_PAID=1")
            run_agent(args.binary, args.operations, args.comm, args.timeout)
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
