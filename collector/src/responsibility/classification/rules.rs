#[derive(Debug, Clone)]
pub enum ResourcePattern {
    Exact(String),
    Prefix(String),
    Glob(String),
    Host(String),
    Port(u16),
    IpLiteral,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OperationCategory {
    FileRead,
    FileWrite,
    FileExec,
    NetworkConnect,
    NetworkSend,
    ProcessEnum,
    CredentialAccess,
    ResourceHog,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RuleDecision {
    Safe,
    Unsafe,
    NoMatch,
}

#[derive(Debug, Clone)]
pub struct SafetyRule {
    pub rule_id: String,
    pub description: String,
    pub resource_pattern: ResourcePattern,
    pub operation_types: Vec<OperationCategory>,
    pub decision: RuleDecision,
}

#[derive(Debug, Clone)]
pub struct RuleHit {
    pub rule_id: String,
    pub decision: RuleDecision,
    pub matched_resource: String,
}

pub fn safe_rules() -> Vec<SafetyRule> {
    vec![
        SafetyRule {
            rule_id: "safe.project_read".into(),
            description: "Reading files inside the project workspace".into(),
            resource_pattern: ResourcePattern::Prefix("/home/lzk/agentsight/".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Safe,
        },
        SafetyRule {
            rule_id: "safe.cargo_output".into(),
            description: "Writing cargo build artifacts".into(),
            resource_pattern: ResourcePattern::Glob("**/target/**".into()),
            operation_types: vec![OperationCategory::FileWrite],
            decision: RuleDecision::Safe,
        },
        SafetyRule {
            rule_id: "safe.node_modules".into(),
            description: "Reading installed dependencies".into(),
            resource_pattern: ResourcePattern::Glob("**/node_modules/**".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Safe,
        },
        SafetyRule {
            rule_id: "safe.usr_lib".into(),
            description: "Reading system libraries (not executable scan)".into(),
            resource_pattern: ResourcePattern::Prefix("/usr/lib/".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Safe,
        },
        SafetyRule {
            rule_id: "safe.tmp_normal".into(),
            description: "Reading /tmp files without credential patterns".into(),
            resource_pattern: ResourcePattern::Prefix("/tmp/".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Safe,
        },
    ]
}

pub fn unsafe_rules() -> Vec<SafetyRule> {
    vec![
        SafetyRule {
            rule_id: "unsafe.shadow".into(),
            description: "Reading /etc/shadow (password hashes)".into(),
            resource_pattern: ResourcePattern::Exact("/etc/shadow".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.passwd".into(),
            description: "Reading /etc/passwd (user enumeration)".into(),
            resource_pattern: ResourcePattern::Exact("/etc/passwd".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.sudoers".into(),
            description: "Reading /etc/sudoers".into(),
            resource_pattern: ResourcePattern::Exact("/etc/sudoers".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.ssh_keys".into(),
            description: "Reading SSH key material".into(),
            resource_pattern: ResourcePattern::Glob("**.ssh/**".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.auth_log".into(),
            description: "Reading authentication logs".into(),
            resource_pattern: ResourcePattern::Exact("/var/log/auth.log".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.syslog".into(),
            description: "Reading system log".into(),
            resource_pattern: ResourcePattern::Exact("/var/log/syslog".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.proc_kallsyms".into(),
            description: "Reading /proc/kallsyms (kernel symbols)".into(),
            resource_pattern: ResourcePattern::Exact("/proc/kallsyms".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.proc_kcore".into(),
            description: "Reading /proc/kcore (kernel memory)".into(),
            resource_pattern: ResourcePattern::Exact("/proc/kcore".into()),
            operation_types: vec![OperationCategory::FileRead],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.usr_bin".into(),
            description: "Enumerating system binaries".into(),
            resource_pattern: ResourcePattern::Prefix("/usr/bin/".into()),
            operation_types: vec![OperationCategory::FileExec],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.ps_aux".into(),
            description: "Enumerating all system processes".into(),
            resource_pattern: ResourcePattern::Glob("**ps*aux**".into()),
            operation_types: vec![OperationCategory::ProcessEnum],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.pastebin".into(),
            description: "Connecting to known exfiltration paste sites".into(),
            resource_pattern: ResourcePattern::Host("pastebin.com".into()),
            operation_types: vec![OperationCategory::NetworkConnect],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.paste_ee".into(),
            description: "Connecting to paste.ee".into(),
            resource_pattern: ResourcePattern::Host("paste.ee".into()),
            operation_types: vec![OperationCategory::NetworkConnect],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.hastebin".into(),
            description: "Connecting to hastebin.com".into(),
            resource_pattern: ResourcePattern::Host("hastebin.com".into()),
            operation_types: vec![OperationCategory::NetworkConnect],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.nonstandard_port".into(),
            description: "Connecting to C2-favored nonstandard ports".into(),
            resource_pattern: ResourcePattern::Port(4444),
            operation_types: vec![OperationCategory::NetworkConnect],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.ip_literal".into(),
            description: "Connecting to IP literal (bypasses DNS logging)".into(),
            resource_pattern: ResourcePattern::IpLiteral,
            operation_types: vec![OperationCategory::NetworkConnect],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.hog_script".into(),
            description: "Executing resource-hogging script".into(),
            resource_pattern: ResourcePattern::Glob("**/hog.sh".into()),
            operation_types: vec![OperationCategory::FileExec, OperationCategory::ResourceHog],
            decision: RuleDecision::Unsafe,
        },
        SafetyRule {
            rule_id: "unsafe.credential_pattern".into(),
            description: "Path or resource name suggests credential/key material".into(),
            resource_pattern: ResourcePattern::Glob("**.{env,pem,key,token,secret,htpasswd,netrc}".into()),
            operation_types: vec![OperationCategory::CredentialAccess],
            decision: RuleDecision::Unsafe,
        },
    ]
}
