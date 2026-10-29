// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project
//
// envelope.rs — capability envelope derived from the user's task.
//
// Progent's lesson: least privilege must be explicit and may only shrink
// between user approvals. The monitor cannot block, but it can attach a
// capability verdict to every action: "the user's task never asked for network
// egress / a shell / a write outside the workspace". That verdict is evidence,
// not a decision — the Observer and the evidence aggregator decide.
//
// The derivation is deliberately conservative and auditable: tools come from
// the first turn's tool calls, hosts and paths from the user-authored text,
// and shell use requires an explicit execution verb.

use std::collections::HashSet;
use std::path::{Component, Path};

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CapabilityVerdict {
    Allowed,
    /// The action introduces a capability the task never requested.
    Expansion(Vec<String>),
    /// The captured evidence cannot decide: e.g. the eBPF producer caps a path
    /// at `MAX_FILENAME_LEN`, so a long in-workspace path arrives truncated and
    /// must not be reported as an expansion.
    Unknown(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapabilityEnvelope {
    #[serde(serialize_with = "serialize_sorted_set")]
    pub tools: HashSet<String>,
    #[serde(serialize_with = "serialize_sorted_set")]
    pub network_hosts: HashSet<String>,
    /// Path prefixes the task is allowed to write (workspace + /tmp by default).
    pub write_prefixes: Vec<String>,
    pub shell_authorized: bool,
    /// Where the envelope came from: `user_task` or `parent:<agent>`.
    pub source: String,
}

fn serialize_sorted_set<S: serde::Serializer>(
    values: &HashSet<String>,
    serializer: S,
) -> Result<S::Ok, S::Error> {
    let mut ordered: Vec<_> = values.iter().collect();
    ordered.sort();
    ordered.serialize(serializer)
}

impl CapabilityEnvelope {
    /// Build an envelope from the user-authored task text and the first turn's
    /// tool names.
    pub fn derive_from_task(trusted_text: &str, tool_names: &[String]) -> Self {
        let lower = trusted_text.to_lowercase();
        let tools: HashSet<String> = tool_names.iter().cloned().collect();
        let network_hosts: HashSet<String> = extract_hosts(trusted_text).into_iter().collect();
        let shell_authorized = tools.iter().any(|t| t.eq_ignore_ascii_case("bash"))
            || [
                "run", "execute", "bash", "shell", "command", "script", "npm ", "make ", "build",
            ]
            .iter()
            .any(|needle| lower.contains(needle));
        // The writable surface is the working directory, the temporary
        // directory, and any path the task names. The whole of $HOME used to
        // be allowed, so a write to ~/.ssh or ~/.config never counted as an
        // expansion even though the task never mentioned it.
        // The workspace is the agent's own working directory. The collector's cwd
        // is not a substitute: a launcher (service manager, harness isolation root)
        // can run it from anywhere, which used to make every in-workspace write an
        // "expansion".
        let mut write_prefixes = vec!["/tmp/".to_string()];
        let workspace = crate::responsibility::workspace::configured_workspace_root();
        if workspace != "\u{0}" && !workspace.is_empty() {
            write_prefixes.push(workspace.clone());
        }
        eprintln!(
            "[envelope] workspace={} write_prefixes={:?}",
            workspace, write_prefixes
        );
        for path in extract_paths(trusted_text) {
            if path.ends_with('/') || path.contains('.') {
                write_prefixes.push(path);
            }
        }
        Self {
            tools,
            network_hosts,
            write_prefixes,
            shell_authorized,
            source: "user_task".into(),
        }
    }

    /// A delegated child may only use what the parent allowed, intersected
    /// with what the child's own task asked for.
    pub fn attenuate(&self, child_task: &str, child_tools: &[String]) -> Self {
        let child = Self::derive_from_task(child_task, child_tools);
        let mut attenuated = self.intersect(&child);
        attenuated.source = "parent_attenuated".into();
        attenuated
    }

    /// Merge identities without granting the union of their capabilities.
    pub fn intersect(&self, other: &Self) -> Self {
        let mut prefixes = Vec::new();
        for left in &self.write_prefixes {
            for right in &other.write_prefixes {
                if let Some(narrower) = narrower_prefix(left, right) {
                    prefixes.push(narrower.to_string());
                }
            }
        }
        prefixes.sort();
        prefixes.dedup();
        Self {
            tools: self.tools.intersection(&other.tools).cloned().collect(),
            network_hosts: self
                .network_hosts
                .intersection(&other.network_hosts)
                .cloned()
                .collect(),
            write_prefixes: prefixes,
            shell_authorized: self.shell_authorized && other.shell_authorized,
            source: "identity_intersection".into(),
        }
    }

    pub fn check_shell(&self) -> CapabilityVerdict {
        if self.shell_authorized {
            CapabilityVerdict::Allowed
        } else {
            CapabilityVerdict::Expansion(vec!["spawn:shell".into()])
        }
    }

    pub fn check_network(&self, host: &str) -> CapabilityVerdict {
        let host = host.trim().to_lowercase();
        if host.is_empty() || is_loopback(&host) || self.network_hosts.contains(&host) {
            return CapabilityVerdict::Allowed;
        }
        // A raw address in a private/loopback/link-local range is a local
        // service, not an unnamed external destination. Comparing the literal
        // against host names marked every container and LAN call an expansion.
        if host
            .trim_matches(|c| c == '[' || c == ']')
            .parse::<std::net::IpAddr>()
            .is_ok_and(is_non_public_address)
        {
            return CapabilityVerdict::Allowed;
        }
        // A host may be named as a parent domain (`example.com` authorizes
        // `api.example.com`, but not `example.com.evil`).
        if self
            .network_hosts
            .iter()
            .any(|allowed| host.ends_with(&format!(".{allowed}")) || host == *allowed)
        {
            return CapabilityVerdict::Allowed;
        }
        CapabilityVerdict::Expansion(vec![format!("network:{host}")])
    }

    pub fn check_write(&self, path: &str) -> CapabilityVerdict {
        if path.is_empty() {
            return CapabilityVerdict::Unknown("empty write path".into());
        }
        // `/proc/self/fd/<n>/...`, `/dev/fd/<n>/...` and `/proc/<pid>/fd/<n>/...`
        // name their target through an already-open descriptor. The spelling does
        // not prove which directory the write reaches, so it cannot be allowed.
        if is_descriptor_indirection(path) {
            return CapabilityVerdict::Unknown(format!(
                "write path resolves through an open descriptor: {path}"
            ));
        }
        // `..` depends on symlink and lookup semantics, not on the spelling.
        if Path::new(path)
            .components()
            .any(|component| component == Component::ParentDir)
        {
            return CapabilityVerdict::Unknown(format!(
                "write path contains a parent-directory component: {path}"
            ));
        }
        // A relative path is relative to the agent's working directory, which
        // is what the envelope authorizes. Comparing the raw relative string
        // against absolute prefixes rejected every in-workspace write.
        let absolute = if Path::new(path).is_absolute() {
            path.to_string()
        } else {
            let workspace = crate::responsibility::workspace::configured_workspace_root();
            let root = workspace.trim_end_matches('/');
            if root.is_empty() || root == "\u{0}" {
                path.to_string()
            } else {
                format!("{root}/{path}")
            }
        };
        // The producer stores at most `CAPTURED_PATH_MAX_CHARS` bytes of a path.
        // A longer path arrives truncated, so no prefix may authorize it.
        if absolute.len() >= CAPTURED_PATH_MAX_CHARS {
            return CapabilityVerdict::Unknown(format!(
                "write path may be truncated at the capture limit: {absolute}"
            ));
        }
        // A device path's target cannot be resolved without the live filesystem.
        if Path::new(&absolute).starts_with("/dev") {
            return CapabilityVerdict::Unknown(format!(
                "write path names an unresolved device: {absolute}"
            ));
        }
        if self
            .write_prefixes
            .iter()
            .any(|prefix| is_under_prefix(&absolute, prefix))
        {
            return CapabilityVerdict::Allowed;
        }
        CapabilityVerdict::Expansion(vec![format!("write:{path}")])
    }
}

/// The longest path the eBPF producer keeps (`MAX_FILENAME_LEN - 1` in
/// `bpftools/process/process.h`). A path at this length may be truncated.
pub const CAPTURED_PATH_MAX_CHARS: usize = 126;

/// Split an absolute path into components for lexical prefix checks. Relative
/// paths and paths containing `..` are malformed here: their target depends on
/// lookup semantics, not on the spelling.
fn absolute_components(path: &str) -> Option<Vec<Component<'_>>> {
    let path = Path::new(path);
    if !path.is_absolute() {
        return None;
    }
    let mut components = Vec::new();
    for component in path.components() {
        match component {
            Component::RootDir | Component::Normal(_) => components.push(component),
            Component::CurDir => {}
            Component::ParentDir | Component::Prefix(_) => return None,
        }
    }
    Some(components)
}

fn is_under_prefix(path: &str, prefix: &str) -> bool {
    let (Some(path), Some(prefix)) = (absolute_components(path), absolute_components(prefix))
    else {
        return false;
    };
    path.starts_with(&prefix)
}

/// The narrower of two comparable prefixes, rejecting malformed ones.
fn narrower_prefix<'a>(left: &'a str, right: &'a str) -> Option<&'a str> {
    let left_components = absolute_components(left)?;
    let right_components = absolute_components(right)?;
    if left_components.starts_with(&right_components) {
        Some(left)
    } else if right_components.starts_with(&left_components) {
        Some(right)
    } else {
        None
    }
}

/// Whether a path reaches its target through an open file descriptor.
fn is_descriptor_indirection(path: &str) -> bool {
    fn numeric(segment: &str) -> bool {
        !segment.is_empty() && segment.bytes().all(|byte| byte.is_ascii_digit())
    }
    fn process(segment: &str) -> bool {
        matches!(segment, "self" | "thread-self") || numeric(segment)
    }
    let Some(components) = absolute_components(path) else {
        return false;
    };
    let names: Vec<_> = components
        .iter()
        .filter_map(|component| match component {
            Component::Normal(name) => name.to_str(),
            _ => None,
        })
        .collect();
    match names.as_slice() {
        ["dev", "fd", fd, ..] => numeric(fd),
        ["proc", owner, "fd", fd, ..] => process(owner) && numeric(fd),
        ["proc", owner, "task", tid, "fd", fd, ..] => process(owner) && numeric(tid) && numeric(fd),
        _ => false,
    }
}

/// Whether an address belongs to a non-public range.
///
/// Shared with the rule engine: a private address is a local destination in
/// both the policy rules and the capability envelope.
/// Whether a host is a local destination (loopback or a non-public address).
///
/// Local destinations are allowed by default because ordinary work talks to local
/// services. The authorization question changes when a *spawned* process sends
/// data there: that is data leaving the agent's own boundary, which the review
/// layer can examine.
pub fn is_local_destination(host: &str) -> bool {
    let host = host.trim().to_lowercase();
    if host.is_empty() || is_loopback(&host) {
        return true;
    }
    host.trim_matches(|c| c == '[' || c == ']')
        .parse::<std::net::IpAddr>()
        .is_ok_and(is_non_public_address)
}

pub fn is_non_public_address(address: std::net::IpAddr) -> bool {
    match address {
        std::net::IpAddr::V4(v4) => {
            v4.is_private()
                || v4.is_loopback()
                || v4.is_link_local()
                || v4.is_broadcast()
                || v4.is_unspecified()
                || {
                    let octets = v4.octets();
                    octets[0] == 100 && (64..=127).contains(&octets[1])
                }
        }
        std::net::IpAddr::V6(v6) => {
            v6.is_loopback()
                || v6.is_unspecified()
                || (v6.segments()[0] & 0xfe00) == 0xfc00
                || (v6.segments()[0] & 0xffc0) == 0xfe80
        }
    }
}

fn is_loopback(host: &str) -> bool {
    host == "localhost" || host == "::1" || host == "[::1]" || host.starts_with("127.")
}

fn extract_hosts(text: &str) -> Vec<String> {
    let mut hosts = Vec::new();
    for token in text.split_whitespace() {
        let token = token.trim_matches(|c: char| {
            matches!(c, '"' | '\'' | '`' | ',' | ';' | ')' | '(' | '>' | '<')
        });
        let candidate = token
            .strip_prefix("https://")
            .or_else(|| token.strip_prefix("http://"))
            .unwrap_or(token);
        let host = candidate
            .split(['/', ':', '?', '#'])
            .next()
            .unwrap_or("")
            .to_lowercase();
        if host.contains('.')
            && host
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-')
            && !host.starts_with('.')
        {
            hosts.push(host);
        }
    }
    hosts
}

fn extract_paths(text: &str) -> Vec<String> {
    let mut paths = Vec::new();
    for token in text.split_whitespace() {
        let token = token.trim_matches(|c: char| {
            matches!(
                c,
                '"' | '\'' | '`' | ',' | ';' | ')' | '(' | '>' | '<' | ':'
            )
        });
        if token.starts_with('/') && !token.contains("://") {
            paths.push(token.to_string());
        }
    }
    paths
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::workspace;

    fn test_envelope(prefixes: &[&str]) -> CapabilityEnvelope {
        CapabilityEnvelope {
            tools: Default::default(),
            network_hosts: Default::default(),
            write_prefixes: prefixes.iter().map(|p| (*p).to_string()).collect(),
            shell_authorized: true,
            source: "test".into(),
        }
    }

    #[test]
    fn descriptor_indirect_writes_are_unknown_not_expansions() {
        let envelope = test_envelope(&["/tmp/"]);
        for path in [
            "/proc/self/fd/13/bfjfn0f9d.output",
            "/proc/1234/fd/11/doc-review-summary.md.tmp.1.ab",
            "/dev/fd/12/out.md",
            "/proc/thread-self/fd/3/out.md",
            "/proc/123/task/456/fd/9/out.md",
            "/proc//self/./fd/3/out.md",
        ] {
            assert!(
                matches!(envelope.check_write(path), CapabilityVerdict::Unknown(_)),
                "{path} names an unresolved descriptor target"
            );
        }
        // The proc path must actually be an fd indirection, not any /proc file.
        assert!(matches!(
            envelope.check_write("/proc/self/environ"),
            CapabilityVerdict::Expansion(_)
        ));
    }

    #[test]
    fn write_prefixes_compare_path_components_not_string_siblings() {
        let envelope = test_envelope(&["/work/project"]);
        assert_eq!(
            envelope.check_write("/work/project/out.md"),
            CapabilityVerdict::Allowed
        );
        assert!(matches!(
            envelope.check_write("/work/project-evil/out.md"),
            CapabilityVerdict::Expansion(_)
        ));
    }

    #[test]
    fn parent_dir_writes_are_unknown_not_authorized() {
        let envelope = test_envelope(&["/tmp/", "/work/project"]);
        for path in ["/tmp/../etc/shadow", "/work/project/../../etc/shadow"] {
            assert!(
                matches!(envelope.check_write(path), CapabilityVerdict::Unknown(_)),
                "{path} depends on lookup semantics"
            );
        }
    }

    #[test]
    fn empty_and_device_paths_are_unknown() {
        let envelope = test_envelope(&["/tmp/"]);
        assert!(matches!(
            envelope.check_write(""),
            CapabilityVerdict::Unknown(_)
        ));
        for path in ["/dev/null", "/dev/sda"] {
            assert!(
                matches!(envelope.check_write(path), CapabilityVerdict::Unknown(_)),
                "{path} is an unresolved device path"
            );
        }
    }

    #[test]
    fn the_capture_limit_is_checked_before_prefix_authorization() {
        let envelope = test_envelope(&["/tmp/"]);
        let long = format!("/tmp/{}", "a".repeat(CAPTURED_PATH_MAX_CHARS));
        assert!(long.len() >= CAPTURED_PATH_MAX_CHARS);
        assert!(matches!(
            envelope.check_write(&long),
            CapabilityVerdict::Unknown(_)
        ));
    }

    #[test]
    fn intersect_keeps_only_the_narrower_valid_prefix() {
        let left = test_envelope(&["/work/project"]);
        let right = test_envelope(&["/work/project/sub"]);
        assert_eq!(
            left.intersect(&right).write_prefixes,
            vec!["/work/project/sub".to_string()]
        );
        let sibling = test_envelope(&["/work/project-evil"]);
        assert!(left.intersect(&sibling).write_prefixes.is_empty());
    }

    #[test]
    fn attenuate_intersects_write_surfaces_instead_of_keeping_the_parent() {
        let parent = test_envelope(&["/work/project"]);
        let child = parent.attenuate("Write /work/project/sub/report.md", &["Write".into()]);
        assert_eq!(child.source, "parent_attenuated");
        assert_eq!(
            child.write_prefixes,
            vec!["/work/project/sub/report.md".to_string()]
        );
    }

    #[test]
    fn a_capture_truncated_path_is_unknown_not_an_expansion() {
        // A deep workspace, like the harness's run directory: its own path plus a
        // short file name stays within the capture cap, a long one cannot.
        let workspace = format!("/{}", "deep/".repeat(20));
        let envelope = CapabilityEnvelope {
            tools: Default::default(),
            network_hosts: Default::default(),
            write_prefixes: vec!["/tmp/".to_string(), workspace.clone()],
            shell_authorized: true,
            source: "test".into(),
        };
        let inside = format!("{workspace}out/result.md");
        assert!(inside.chars().count() < CAPTURED_PATH_MAX_CHARS);
        assert!(matches!(
            envelope.check_write(&inside),
            CapabilityVerdict::Allowed
        ));
        // Outside the workspace and at the cap: the capture may have cut it, so
        // the monitor cannot prove it is outside.
        let over_cap = format!("/elsewhere/{}", "y".repeat(140));
        assert!(over_cap.chars().count() > CAPTURED_PATH_MAX_CHARS);
        assert!(matches!(
            envelope.check_write(&over_cap),
            CapabilityVerdict::Unknown(_)
        ));
        // A short path outside the workspace is still an expansion.
        assert!(matches!(
            envelope.check_write("/home/other/.ssh/id_rsa"),
            CapabilityVerdict::Expansion(_)
        ));
    }

    #[test]
    fn the_capture_limit_is_counted_in_bytes_not_chars() {
        let envelope = CapabilityEnvelope {
            tools: Default::default(),
            network_hosts: Default::default(),
            write_prefixes: vec!["/tmp/".to_string()],
            shell_authorized: true,
            source: "test".into(),
        };
        // 1 + 62 * 2 + 1 = 126 bytes, but only 64 characters.
        let path = format!("/{}x", "é".repeat(62));
        assert_eq!(path.len(), CAPTURED_PATH_MAX_CHARS);
        assert!(path.chars().count() < CAPTURED_PATH_MAX_CHARS);
        assert!(matches!(
            envelope.check_write(&path),
            CapabilityVerdict::Unknown(_)
        ));
    }

    #[test]
    fn shell_requires_an_execution_verb_or_bash_tool() {
        let readonly =
            CapabilityEnvelope::derive_from_task("Summarize this document.", &["Read".into()]);
        assert!(matches!(
            readonly.check_shell(),
            CapabilityVerdict::Expansion(_)
        ));

        let runner =
            CapabilityEnvelope::derive_from_task("Run the build and report.", &["Bash".into()]);
        assert_eq!(runner.check_shell(), CapabilityVerdict::Allowed);
    }

    #[test]
    fn network_hosts_come_from_the_user_task() {
        let envelope = CapabilityEnvelope::derive_from_task(
            "Fetch https://api.example.com/v1/items and summarize.",
            &["Bash".into()],
        );
        assert_eq!(
            envelope.check_network("api.example.com"),
            CapabilityVerdict::Allowed
        );
        assert_eq!(
            envelope.check_network("evil.example.net"),
            CapabilityVerdict::Expansion(vec!["network:evil.example.net".into()])
        );
        // A domain the user named does not authorize a lookalike suffix.
        assert!(matches!(
            envelope.check_network("api.example.com.evil.test"),
            CapabilityVerdict::Expansion(_)
        ));
    }

    #[test]
    fn loopback_and_tmp_are_always_allowed() {
        let envelope = CapabilityEnvelope::derive_from_task("Summarize.", &["Read".into()]);
        assert_eq!(
            envelope.check_network("127.0.0.1"),
            CapabilityVerdict::Allowed
        );
        assert_eq!(
            envelope.check_network("localhost"),
            CapabilityVerdict::Allowed
        );
        assert_eq!(
            envelope.check_write("/tmp/report.md"),
            CapabilityVerdict::Allowed
        );
    }

    #[test]
    fn private_addresses_are_local_destinations_not_expansions() {
        let envelope = CapabilityEnvelope::derive_from_task("Summarize.", &["Read".into()]);
        for host in [
            "10.0.0.5",
            "192.168.1.20",
            "169.254.169.254",
            "100.64.1.1",
            "fd00::1",
        ] {
            assert_eq!(
                envelope.check_network(host),
                CapabilityVerdict::Allowed,
                "{host} is a private address"
            );
        }
        assert!(matches!(
            envelope.check_network("93.184.216.34"),
            CapabilityVerdict::Expansion(_)
        ));
    }

    #[test]
    fn a_write_inside_the_workspace_is_allowed_but_home_is_not() {
        let envelope = CapabilityEnvelope::derive_from_task("Summarize.", &["Write".into()]);
        let workspace = workspace::configured_workspace_root();
        let inside = format!("{workspace}notes.md");
        assert_eq!(envelope.check_write(&inside), CapabilityVerdict::Allowed);
        assert_eq!(
            envelope.check_write("notes.md"),
            CapabilityVerdict::Allowed,
            "relative paths resolve against the workspace"
        );
        let home = std::env::var("HOME").unwrap_or_else(|_| "/home/nobody".into());
        assert!(matches!(
            envelope.check_write(&format!("{home}/.ssh/authorized_keys")),
            CapabilityVerdict::Expansion(_)
        ));
    }

    #[test]
    fn writes_outside_the_workspace_are_expansions() {
        let envelope = CapabilityEnvelope::derive_from_task("Summarize.", &["Write".into()]);
        assert!(matches!(
            envelope.check_write("/etc/cron.d/backdoor"),
            CapabilityVerdict::Expansion(_)
        ));
    }

    #[test]
    fn a_child_envelope_cannot_exceed_its_parent() {
        let parent = CapabilityEnvelope::derive_from_task(
            "Run curl https://api.example.com/x",
            &["Bash".into()],
        );
        let child = parent.attenuate("Read the file and summarize.", &["Read".into()]);
        assert!(!child.shell_authorized);
        assert!(child.network_hosts.is_empty());
        assert_eq!(
            child.check_network("api.example.com"),
            CapabilityVerdict::Expansion(vec!["network:api.example.com".into()])
        );
    }
}
