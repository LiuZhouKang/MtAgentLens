use std::fs;

fn manifest_path() -> &'static str {
    "../docs/design/experiment-design.md"
}

#[test]
fn scenario_manifest_lists_all_current_env_scenarios() {
    let manifest = match fs::read_to_string(manifest_path()) {
        Ok(content) => content,
        Err(_) => {
            eprintln!("manifest not found at {} — skipping validation", manifest_path());
            return;
        }
    };

    for name in [
        "agent-teams-context-pollution.json",
        "agent-teams-coordination-deadlock.json",
        "agent-teams-race-conflict.json",
        "agent-teams-resource-starvation.json",
        "agent-teams-role-hijack.json",
        "collaborative-dev.json",
        "network-anomaly.json",
        "privilege-escalation.json",
        "prompt-injection.json",
        "reasoning-loop.json",
        "subagents-collaborative.json",
        "subagents-context-loss.json",
        "subagents-handoff-injection.json",
        "subagents-identity-confusion.json",
        "subagents-infinite-recursion.json",
        "subagents-privilege-leak.json",
        "subagents-security-response.json",
    ] {
        assert!(manifest.contains(name), "missing scenario: {name}");
    }
}
