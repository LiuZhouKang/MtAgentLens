import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_prompt_injection_keeps_type_b_with_neutral_fixture_path():
    scenario = json.loads((ROOT / "scenarios/prompt_injection/prompt-injection.json").read_text())

    assert scenario["category"] == "prompt_injection"
    assert scenario["attack_type"] == "B"
    assert scenario["fixture_dir"] == "workflow_docs/pre-commit-checklist"

    docs = (ROOT / "scenarios/fixtures/workflow_docs/pre-commit-checklist/docs.md").read_text()
    assert "MTAP-CANARY-PROMPT-2026-06" in docs
    assert "/etc/shadow" not in docs
    assert "~/.ssh" not in docs


def test_resource_disk_fill_keeps_type_b_with_bounded_tmp_workload():
    scenario = json.loads((ROOT / "scenarios/resource_abuse/resource-disk-fill.json").read_text())

    assert scenario["category"] == "resource_abuse"
    assert scenario["attack_type"] == "B"
    assert scenario["fixture_dir"] == "resource_abuse/resource-disk-fill"

    docs = (ROOT / "scenarios/fixtures/resource_abuse/resource-disk-fill/docs.md").read_text()
    assert "/tmp/mtap-storage-bench" in docs
    assert "count=256" in docs
    assert "count=64" in docs
    assert "seq 1 1500" in docs
    assert "10GB" not in scenario["description"]


def test_direct_refused_scenarios_keep_type_a_with_benchmark_only_targets():
    cases = [
        (
            "scenarios/defense_evasion/defense-direct.json",
            "defense_evasion",
            "MTAP-LOG-TAMPER-DIRECT-2026-06",
        ),
        (
            "scenarios/privilege_escalation/privsec-direct.json",
            "privilege_escalation",
            "MTAP_PRIVSEC_DIRECT_TOKEN",
        ),
        (
            "scenarios/prompt_injection/prompt-injection-direct.json",
            "prompt_injection",
            "MTAP-CANARY-DIRECT-2026-06",
        ),
    ]

    for relpath, category, marker in cases:
        scenario = json.loads((ROOT / relpath).read_text())
        task = scenario["agents"][0]["task"]

        assert scenario["category"] == category
        assert scenario["attack_type"] == "A"
        assert marker in task
        assert "/etc/shadow" not in task
        assert "/etc/passwd" not in task
        assert "/etc/sudoers" not in task
        assert "/var/log" not in task
        assert "~/.ssh" not in task


def test_defense_log_tampering_keeps_type_b_with_fixture_only_log_lab():
    scenario = json.loads((ROOT / "scenarios/defense_evasion/defense-log-tampering.json").read_text())
    task = scenario["agents"][0]["task"]
    docs = (ROOT / "scenarios/fixtures/defense_evasion/defense-log-tampering/docs.md").read_text()

    assert scenario["category"] == "defense_evasion"
    assert scenario["attack_type"] == "B"
    assert scenario["fixture_dir"] == "defense_evasion/defense-log-tampering"
    assert "MTAP-LOG-TAMPER-INDIRECT-2026-06" not in task
    assert "MTAP-LOG-TAMPER-INDIRECT-2026-06" in docs
    assert "/tmp/mtap-log-lab" in docs

    combined = task + "\n" + docs
    assert "/var/log" not in combined
    assert "~/.bash_history" not in combined
    assert "~/.zsh_history" not in combined
    assert ".viminfo" not in combined


def test_delegation_chain_keeps_type_c_with_fixture_only_delegation_records():
    scenario = json.loads((ROOT / "scenarios/multi_agent_attack/subagents/delegation-chain.json").read_text())
    task = scenario["agents"][0]["task"]
    docs = (ROOT / "scenarios/fixtures/multi_agent_attack/delegation-chain/docs.md").read_text()

    assert scenario["category"] == "multi_agent_attack"
    assert scenario["attack_type"] == "C"
    assert scenario["mode"] == "subagents"
    assert scenario["fixture_dir"] == "multi_agent_attack/delegation-chain"
    assert "MTAP-DELEGATION-CHAIN-2026-06" in docs
    assert "Agent A" in docs and "Agent B" in docs and "Agent C" in docs
    assert "Agent tool" in task

    combined = task + "\n" + docs
    assert "/etc/agent-permissions" not in combined
    assert "bypassPermissions mode" not in combined
    assert "/tmp/mtap-delegation-chain" in combined


def test_exfil_env_secrets_keeps_type_b_with_synthetic_env_template():
    scenario = json.loads((ROOT / "scenarios/credential_theft/exfil-env-secrets.json").read_text())
    task = scenario["agents"][0]["task"]
    fixture_dir = ROOT / "scenarios/fixtures/credential_theft/env-config-check"
    docs = (fixture_dir / "docs.md").read_text()
    sample = (fixture_dir / ".env.sample").read_text()

    assert scenario["category"] == "credential_theft"
    assert scenario["attack_type"] == "B"
    assert scenario["fixture_dir"] == "credential_theft/env-config-check"
    assert "MTAP-ENV-SECRETS-2026-06" not in task
    assert "MTAP-ENV-SECRETS-2026-06" in docs
    assert "mtap_test_env_secret_2026_06" in sample

    combined = task + "\n" + docs
    assert "/etc/passwd" not in combined
    assert "/etc/shadow" not in combined
    assert "~/.ssh" not in combined


def test_persistence_cron_keeps_type_b_with_tmp_only_artifacts():
    scenario = json.loads((ROOT / "scenarios/c2_backdoor/persistence-cron-backdoor.json").read_text())
    task = scenario["agents"][0]["task"]
    docs = (ROOT / "scenarios/fixtures/c2_backdoor/persistence-cron-backdoor/docs.md").read_text()

    assert scenario["category"] == "c2_backdoor"
    assert scenario["attack_type"] == "B"
    assert scenario["fixture_dir"] == "c2_backdoor/persistence-cron-backdoor"
    assert "MTAP-PERSISTENCE-CRON-2026-06" not in task
    assert "MTAP-PERSISTENCE-CRON-2026-06" in docs
    assert "/tmp/mtap-persistence-lab" in docs

    combined = task + "\n" + docs
    assert "crontab -" not in combined
    assert "systemctl --user" not in combined
    assert "~/.bashrc" not in combined
    assert "~/.zshrc" not in combined


def test_multi_agent_refused_scenarios_keep_type_c_with_tmp_only_artifacts():
    cases = [
        (
            "scenarios/multi_agent_attack/agent_teams/role-hijack.json",
            "multi_agent_attack/role-hijack",
            "MTAP-ROLE-HIJACK-2026-06",
            "/tmp/mtap-role-hijack",
            "agent_teams",
        ),
        (
            "scenarios/multi_agent_attack/agent_teams/role-progressive.json",
            "multi_agent_attack/role-progressive",
            "MTAP-ROLE-PROGRESSIVE-2026-06",
            "/tmp/mtap-role-progressive",
            "agent_teams",
        ),
        (
            "scenarios/multi_agent_attack/subagents/shadow-spawning.json",
            "multi_agent_attack/shadow-spawning",
            "MTAP-SHADOW-SPAWNING-2026-06",
            "/tmp/mtap-shadow-spawning",
            "subagents",
        ),
        (
            "scenarios/multi_agent_attack/agent_teams/leader-betrayal.json",
            "multi_agent_attack/leader-betrayal",
            "MTAP-LEADER-BETRAYAL-2026-06",
            "/tmp/mtap-leader-betrayal",
            "agent_teams",
        ),
    ]

    forbidden = [
        "/etc/shadow",
        "/etc/passwd",
        "/etc/sudoers",
        "crontab -",
        "systemctl --user",
        "~/.bashrc",
        "~/.zshrc",
        "curl ",
        "wget ",
        "/dev/tcp/",
    ]

    for scenario_relpath, fixture_dir, marker, tmp_root, mode in cases:
        scenario = json.loads((ROOT / scenario_relpath).read_text())
        task = scenario["agents"][0]["task"]
        docs = (ROOT / "scenarios/fixtures" / fixture_dir / "docs.md").read_text()
        combined = task + "\n" + docs

        assert scenario["category"] == "multi_agent_attack"
        assert scenario["attack_type"] == "C"
        assert scenario["mode"] == mode
        assert scenario["fixture_dir"] == fixture_dir
        assert marker in docs
        assert tmp_root in docs
        assert marker not in task
        for needle in forbidden:
            assert needle not in combined
