// SPDX-License-Identifier: MIT
//
// argument_matcher.rs — L1-L5 argument matching between Intent and Action args.
//
// Performs a Cartesian product comparison between the NormalizedArg sets of
// an Intent and an Action, returning the best match level and a score in [0,1].
//
// Match levels (spec §7.3):
//   L1Exact      (0.90) — exact path/url/command equality
//   L2Normalized (0.75) — normalized path equality (symlink/~ expansion)
//   L3Containment(0.65) — host match (URL) or prefix/suffix containment
//   L4Basename   (0.50) — file basename or command basename match
//   L5Substring  (0.35) — substring match (command >=3 chars, path >=8 chars)
//
// Symmetric: Command matching checks both intent→action and action→intent
// directions to handle cases where the intent mentions "python3" and the
// action executes "/usr/bin/python3".

use crate::responsibility::types::{MatchLevel, NormalizedArg};

/// Score associated with each match level.
pub fn match_level_base_score(level: &MatchLevel) -> f32 {
    match level {
        MatchLevel::L1Exact => 0.90,
        MatchLevel::L2Normalized => 0.75,
        MatchLevel::L3Containment => 0.65,
        MatchLevel::L4Basename => 0.50,
        MatchLevel::L5Substring => 0.35,
    }
}

/// Convert a MatchLevel to its numeric ordinal for comparison.
fn match_level_ord(level: &MatchLevel) -> u8 {
    match level {
        MatchLevel::L1Exact => 5,
        MatchLevel::L2Normalized => 4,
        MatchLevel::L3Containment => 3,
        MatchLevel::L4Basename => 2,
        MatchLevel::L5Substring => 1,
    }
}

/// Minimum substring length for L5: 3 chars for commands, 8 chars for paths.
const MIN_COMMAND_SUBSTRING: usize = 3;
const MIN_PATH_SUBSTRING: usize = 8;

/// Match intent args against action args using a Cartesian product.
///
/// Returns the best (MatchLevel, score) pair, or `None` if no match is found.
///
/// The `base_score` for each level is the starting point; the returned score
/// is always the base (confidence scaling is done in `confidence.rs`).
pub fn match_args(
    intent_args: &[NormalizedArg],
    action_args: &[NormalizedArg],
) -> Option<(MatchLevel, f32)> {
    if intent_args.is_empty() || action_args.is_empty() {
        return None;
    }

    let mut best_level: Option<MatchLevel> = None;
    let mut best_score: f32 = 0.0;

    for i_arg in intent_args {
        for a_arg in action_args {
            if let Some((level, score)) = compare_args(i_arg, a_arg) {
                let ord = match_level_ord(&level);
                let current_best_ord = best_level.as_ref().map(|l| match_level_ord(l)).unwrap_or(0);
                if ord > current_best_ord || (ord == current_best_ord && score > best_score) {
                    best_level = Some(level);
                    best_score = score;
                }
            }
        }
    }

    best_level.map(|l| (l, best_score))
}

/// Compare two individual NormalizedArg values.
///
/// Returns the highest match level achievable between the two args.
/// Comparison is domain-specific per NormalizedArg variant.
fn compare_args(intent: &NormalizedArg, action: &NormalizedArg) -> Option<(MatchLevel, f32)> {
    match (intent, action) {
        // Path ↔ Path
        (NormalizedArg::Path(ip), NormalizedArg::Path(ap)) => compare_paths(ip, ap),

        // Command ↔ Command (symmetric)
        (NormalizedArg::Command(ic), NormalizedArg::Command(ac)) => compare_commands(ic, ac),

        // Command ↔ Path: the command might be a basename of a path
        (NormalizedArg::Command(ic), NormalizedArg::Path(ap)) => compare_command_to_path(ic, ap),
        (NormalizedArg::Path(ip), NormalizedArg::Command(ac)) => compare_command_to_path(ac, ip),

        // Url ↔ Url
        (NormalizedArg::Url { host: ih, port: ip }, NormalizedArg::Url { host: ah, port: ap }) => {
            compare_urls(ih, *ip, ah, *ap)
        }

        // Cross-type (including Content — not matchable to action args)
        (NormalizedArg::Url { .. }, NormalizedArg::Path(_))
        | (NormalizedArg::Url { .. }, NormalizedArg::Command(_))
        | (NormalizedArg::Path(_), NormalizedArg::Url { .. })
        | (NormalizedArg::Command(_), NormalizedArg::Url { .. })
        | (NormalizedArg::Content(_), _)
        | (_, NormalizedArg::Content(_)) => None,
    }
}

// ─── Path comparison ──────────────────────────────────────────────────

fn compare_paths(intent_path: &str, action_path: &str) -> Option<(MatchLevel, f32)> {
    // L1: exact match
    if intent_path == action_path {
        return Some((MatchLevel::L1Exact, 0.90));
    }

    // L2: normalized match
    let ni = normalize_path(intent_path);
    let na = normalize_path(action_path);
    if ni == na {
        return Some((MatchLevel::L2Normalized, 0.75));
    }

    // L3: containment — one is a prefix/suffix of the other
    if ni.contains(&na) || na.contains(&ni) {
        return Some((MatchLevel::L3Containment, 0.65));
    }

    // L4: basename match
    let bi = path_basename(&ni);
    let ba = path_basename(&na);
    if !bi.is_empty() && !ba.is_empty() && bi == ba {
        return Some((MatchLevel::L4Basename, 0.50));
    }

    // L5: substring match (min 8 chars)
    if ni.len() >= MIN_PATH_SUBSTRING && na.len() >= MIN_PATH_SUBSTRING {
        if ni.contains(&na) || na.contains(&ni) {
            return Some((MatchLevel::L5Substring, 0.35));
        }
        // Try partial substring: find overlapping substrings of length >= 8
        if longest_common_substring(&ni, &na, MIN_PATH_SUBSTRING).is_some() {
            return Some((MatchLevel::L5Substring, 0.35));
        }
    }

    None
}

// ─── Command comparison (symmetric) ───────────────────────────────────

fn compare_commands(intent_cmd: &str, action_cmd: &str) -> Option<(MatchLevel, f32)> {
    // L1: exact match
    if intent_cmd == action_cmd {
        return Some((MatchLevel::L1Exact, 0.90));
    }

    // L2: lowercased match
    let il = intent_cmd.to_lowercase();
    let al = action_cmd.to_lowercase();
    if il == al {
        return Some((MatchLevel::L2Normalized, 0.75));
    }

    // L3: containment
    if il.contains(&al) || al.contains(&il) {
        return Some((MatchLevel::L3Containment, 0.65));
    }

    // L4: basename match (already handled by exact/lowercase at this point,
    // but also check for commands with path prefixes)
    // In practice, Command args are already basenames, so L4 is same as L3 here

    // L5: substring (min 3 chars)
    if il.len() >= MIN_COMMAND_SUBSTRING && al.len() >= MIN_COMMAND_SUBSTRING {
        if longest_common_substring(&il, &al, MIN_COMMAND_SUBSTRING).is_some() {
            return Some((MatchLevel::L5Substring, 0.35));
        }
    }

    None
}

/// Compare a command to a path (e.g., intent says "python3", action has "/usr/bin/python3").
/// This handles the Command↔Path cross-match.
fn compare_command_to_path(cmd: &str, path: &str) -> Option<(MatchLevel, f32)> {
    let cmd_lower = cmd.to_lowercase();
    let path_lower = path.to_lowercase();

    // Extract the basename of the path and compare
    let basename = path_basename(path);

    // L1: command exactly equals path basename
    if cmd == basename {
        return Some((MatchLevel::L1Exact, 0.90));
    }
    if cmd_lower == basename.to_lowercase() {
        return Some((MatchLevel::L2Normalized, 0.75));
    }

    // L3: command is contained in path
    if path_lower.contains(&cmd_lower) {
        return Some((MatchLevel::L3Containment, 0.65));
    }

    // L4: path basename contains command or vice versa
    let base_lower = basename.to_lowercase();
    if base_lower.contains(&cmd_lower) || cmd_lower.contains(&base_lower) {
        return Some((MatchLevel::L4Basename, 0.50));
    }

    // L5: substring (min 3 chars)
    if cmd_lower.len() >= MIN_COMMAND_SUBSTRING && path_lower.len() >= MIN_COMMAND_SUBSTRING {
        if longest_common_substring(&cmd_lower, &path_lower, MIN_COMMAND_SUBSTRING).is_some() {
            return Some((MatchLevel::L5Substring, 0.35));
        }
    }

    None
}

// ─── URL comparison ───────────────────────────────────────────────────

fn compare_urls(
    intent_host: &str,
    intent_port: Option<u16>,
    action_host: &str,
    action_port: Option<u16>,
) -> Option<(MatchLevel, f32)> {
    let ih = intent_host.to_lowercase();
    let ah = action_host.to_lowercase();

    // L1: exact host and port match
    if ih == ah && intent_port == action_port {
        return Some((MatchLevel::L1Exact, 0.90));
    }

    // L2: exact host match (port differs or absent)
    if ih == ah {
        return Some((MatchLevel::L2Normalized, 0.75));
    }

    // L3: host containment (e.g., "evil.com" in "www.evil.com")
    if ih.contains(&ah) || ah.contains(&ih) {
        if intent_port == action_port || intent_port.is_none() || action_port.is_none() {
            return Some((MatchLevel::L3Containment, 0.65));
        }
        // Same containment but ports differ explicitly → weaker
        return Some((MatchLevel::L4Basename, 0.50));
    }

    // L4: port match only (different hosts)
    if intent_port.is_some() && intent_port == action_port {
        return Some((MatchLevel::L4Basename, 0.50));
    }

    // L5: substring match on hostname (min 3 chars)
    if ih.len() >= MIN_COMMAND_SUBSTRING && ah.len() >= MIN_COMMAND_SUBSTRING {
        if longest_common_substring(&ih, &ah, MIN_COMMAND_SUBSTRING).is_some() {
            return Some((MatchLevel::L5Substring, 0.35));
        }
    }

    None
}

// ─── Path normalization ───────────────────────────────────────────────

/// Normalize a path for L2 matching.
///
/// Handles:
/// - Leading `./` removal
/// - Trailing `/` removal
/// - Double slash collapse
/// - Home directory expansion (`~/` → `/root/` or `/home/...`)
///   (in production this would use `dirs::home_dir()`, but for the
///   evidence module we do a conservative expansion to `/root/` for
///   the common sudo case)
fn normalize_path(path: &str) -> String {
    let p = path.trim();

    // Expand ~
    let p = if p.starts_with("~/") {
        // Conservative: expand to /root/ for the typical agentsight case
        format!("/root/{}", &p[2..])
    } else if p == "~" {
        "/root".to_string()
    } else {
        p.to_string()
    };

    // Remove ./
    let p = p.strip_prefix("./").unwrap_or(&p).to_string();

    // Collapse double slashes
    let mut result = String::with_capacity(p.len());
    let mut last_slash = false;
    for ch in p.chars() {
        if ch == '/' {
            if !last_slash {
                result.push('/');
            }
            last_slash = true;
        } else {
            result.push(ch);
            last_slash = false;
        }
    }

    // Strip trailing slash (unless root)
    if result.len() > 1 {
        while result.ends_with('/') {
            result.pop();
        }
    }

    // Resolve .. and . segments
    resolve_dot_segments(&result)
}

/// Resolve `.` and `..` segments in a path.
fn resolve_dot_segments(path: &str) -> String {
    let mut components: Vec<&str> = Vec::new();
    for segment in path.split('/') {
        match segment {
            "" | "." => {}           // skip empty and current-dir
            ".." => {
                components.pop();    // go up one level
            }
            _ => components.push(segment),
        }
    }

    if components.is_empty() {
        return "/".to_string();
    }

    let mut result = String::new();
    for c in components {
        result.push('/');
        result.push_str(c);
    }
    result
}

/// Extract the basename from a path.
fn path_basename(path: &str) -> String {
    let p = path.trim_end_matches('/');
    if let Some(pos) = p.rfind('/') {
        p[pos + 1..].to_string()
    } else {
        p.to_string()
    }
}

/// Find the longest common substring of at least `min_len` characters.
fn longest_common_substring(a: &str, b: &str, min_len: usize) -> Option<(usize, String)> {
    let a_bytes = a.as_bytes();
    let b_bytes = b.as_bytes();
    let mut best_len = 0;
    let mut best_start = 0;

    // Dynamic programming table: only need two rows
    let mut prev = vec![0usize; b.len() + 1];
    let mut curr = vec![0usize; b.len() + 1];

    for i in 0..a.len() {
        for j in 0..b.len() {
            if a_bytes[i] == b_bytes[j] {
                curr[j + 1] = prev[j] + 1;
                if curr[j + 1] > best_len {
                    best_len = curr[j + 1];
                    best_start = i + 1 - best_len;
                }
            } else {
                curr[j + 1] = 0;
            }
        }
        std::mem::swap(&mut prev, &mut curr);
    }

    if best_len >= min_len {
        let substr = a[best_start..best_start + best_len].to_string();
        Some((best_len, substr))
    } else {
        None
    }
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // ── Path matching ────────────────────────────────────────────────

    #[test]
    fn test_path_l1_exact() {
        let result = match_args(
            &[NormalizedArg::Path("/etc/passwd".into())],
            &[NormalizedArg::Path("/etc/passwd".into())],
        );
        assert_eq!(result, Some((MatchLevel::L1Exact, 0.90)));
    }

    #[test]
    fn test_path_l2_normalized_dot_slash() {
        let result = match_args(
            &[NormalizedArg::Path("./etc/passwd".into())],
            &[NormalizedArg::Path("/etc/passwd".into())],
        );
        // ./etc/passwd normalizes to /etc/passwd → L2Normalized match
        assert!(result.is_some());
        let (level, _) = result.unwrap();
        assert!(matches!(level, MatchLevel::L2Normalized));
    }

    #[test]
    fn test_path_l2_normalized_trailing_slash() {
        let result = match_args(
            &[NormalizedArg::Path("/etc/passwd/".into())],
            &[NormalizedArg::Path("/etc/passwd".into())],
        );
        assert!(result.is_some());
        let (level, _) = result.unwrap();
        assert!(matches!(level, MatchLevel::L1Exact | MatchLevel::L2Normalized));
    }

    #[test]
    fn test_path_l4_basename() {
        let result = match_args(
            &[NormalizedArg::Path("/home/user/config.yaml".into())],
            &[NormalizedArg::Path("/tmp/config.yaml".into())],
        );
        assert_eq!(result, Some((MatchLevel::L4Basename, 0.50)));
    }

    #[test]
    fn test_path_no_match() {
        let result = match_args(
            &[NormalizedArg::Path("/etc/passwd".into())],
            &[NormalizedArg::Path("/var/log/syslog".into())],
        );
        assert!(result.is_none());
    }

    // ── Command matching ────────────────────────────────────────────

    #[test]
    fn test_command_l1_exact() {
        let result = match_args(
            &[NormalizedArg::Command("python3".into())],
            &[NormalizedArg::Command("python3".into())],
        );
        assert_eq!(result, Some((MatchLevel::L1Exact, 0.90)));
    }

    #[test]
    fn test_command_l2_case_insensitive() {
        let result = match_args(
            &[NormalizedArg::Command("Python3".into())],
            &[NormalizedArg::Command("python3".into())],
        );
        assert!(result.is_some());
        let (level, _) = result.unwrap();
        assert!(matches!(level, MatchLevel::L2Normalized));
    }

    #[test]
    fn test_command_l3_containment() {
        let result = match_args(
            &[NormalizedArg::Command("python".into())],
            &[NormalizedArg::Command("python3".into())],
        );
        // "python" is fully contained in "python3" → L3Containment
        assert!(result.is_some());
        let (level, _) = result.unwrap();
        assert!(matches!(level, MatchLevel::L3Containment));
    }

    // ── Command ↔ Path cross-matching ────────────────────────────────

    #[test]
    fn test_command_to_path_l1_exact_basename() {
        let result = match_args(
            &[NormalizedArg::Command("curl".into())],
            &[NormalizedArg::Path("/usr/bin/curl".into())],
        );
        assert_eq!(result, Some((MatchLevel::L1Exact, 0.90)));
    }

    #[test]
    fn test_command_to_path_l3_containment() {
        let result = match_args(
            &[NormalizedArg::Command("python".into())],
            &[NormalizedArg::Path("/usr/bin/python3.11".into())],
        );
        // "python" is contained in "python3.11" → L3Containment
        assert!(result.is_some());
        let (level, _) = result.unwrap();
        assert!(matches!(level, MatchLevel::L3Containment));
    }

    // ── URL matching ─────────────────────────────────────────────────

    #[test]
    fn test_url_l1_exact() {
        let result = match_args(
            &[NormalizedArg::Url {
                host: "evil.com".into(),
                port: Some(4444),
            }],
            &[NormalizedArg::Url {
                host: "evil.com".into(),
                port: Some(4444),
            }],
        );
        assert_eq!(result, Some((MatchLevel::L1Exact, 0.90)));
    }

    #[test]
    fn test_url_l2_host_match() {
        let result = match_args(
            &[NormalizedArg::Url {
                host: "evil.com".into(),
                port: Some(8080),
            }],
            &[NormalizedArg::Url {
                host: "evil.com".into(),
                port: Some(4444),
            }],
        );
        assert!(result.is_some());
        let (level, _) = result.unwrap();
        assert!(matches!(level, MatchLevel::L2Normalized));
    }

    #[test]
    fn test_url_l3_containment() {
        let result = match_args(
            &[NormalizedArg::Url {
                host: "evil.com".into(),
                port: None,
            }],
            &[NormalizedArg::Url {
                host: "api.evil.com".into(),
                port: None,
            }],
        );
        assert!(result.is_some());
        let (level, _) = result.unwrap();
        assert!(matches!(level, MatchLevel::L3Containment));
    }

    #[test]
    fn test_url_l4_port_match() {
        let result = match_args(
            &[NormalizedArg::Url {
                host: "evil.com".into(),
                port: Some(1337),
            }],
            &[NormalizedArg::Url {
                host: "safe.com".into(),
                port: Some(1337),
            }],
        );
        assert!(result.is_some());
        let (level, _) = result.unwrap();
        assert!(matches!(level, MatchLevel::L4Basename));
    }

    #[test]
    fn test_url_no_match() {
        let result = match_args(
            &[NormalizedArg::Url {
                host: "evil.com".into(),
                port: None,
            }],
            &[NormalizedArg::Url {
                host: "safe.org".into(),
                port: None,
            }],
        );
        assert!(result.is_none());
    }

    // ── Cartesian product: best match wins ────────────────────────────

    #[test]
    fn test_cartesian_best_match() {
        let intent_args = vec![
            NormalizedArg::Command("ls".into()),
            NormalizedArg::Path("/etc/hosts".into()),
        ];
        let action_args = vec![
            NormalizedArg::Path("/etc/hosts".into()),
            NormalizedArg::Command("cat".into()),
        ];

        // /etc/hosts ↔ /etc/hosts = L1Exact (best)
        // ls ↔ cat = no match
        let result = match_args(&intent_args, &action_args);
        assert_eq!(result, Some((MatchLevel::L1Exact, 0.90)));
    }

    #[test]
    fn test_cartesian_weaker_match() {
        let intent_args = vec![NormalizedArg::Command("python".into())];
        let action_args = vec![
            NormalizedArg::Command("ls".into()),
            NormalizedArg::Path("/usr/bin/python3".into()),
        ];

        // "python" ↔ "ls" = no match
        // "python" ↔ "/usr/bin/python3" = L3Containment
        let result = match_args(&intent_args, &action_args);
        assert!(result.is_some());
        let (level, _) = result.unwrap();
        assert!(matches!(level, MatchLevel::L3Containment));
    }

    #[test]
    fn test_empty_args() {
        let result = match_args(&[], &[NormalizedArg::Path("/etc/hosts".into())]);
        assert!(result.is_none());

        let result = match_args(&[NormalizedArg::Path("/etc/hosts".into())], &[]);
        assert!(result.is_none());
    }

    // ── Path normalization helpers ───────────────────────────────────

    #[test]
    fn test_normalize_path_home_expansion() {
        assert_eq!(normalize_path("~/data"), "/root/data");
        assert_eq!(normalize_path("~"), "/root");
    }

    #[test]
    fn test_normalize_path_dot_segments() {
        assert_eq!(normalize_path("/a/b/../c"), "/a/c");
        assert_eq!(normalize_path("/a/./b"), "/a/b");
        assert_eq!(normalize_path("/a/b/c/../../d"), "/a/d");
    }

    #[test]
    fn test_path_basename_extraction() {
        assert_eq!(path_basename("/etc/passwd"), "passwd");
        assert_eq!(path_basename("/usr/bin/"), "bin");
        assert_eq!(path_basename("passwd"), "passwd");
        assert_eq!(path_basename("/"), "");
    }

    #[test]
    fn test_longest_common_substring() {
        let result = longest_common_substring("python3.11", "python3", 3);
        assert!(result.is_some());
        let (len, substr) = result.unwrap();
        assert!(len >= 3);
        assert!(substr.contains("python"));
    }

    #[test]
    fn test_longest_common_substring_too_short() {
        let result = longest_common_substring("ab", "abc", 3);
        assert!(result.is_none());
    }

    #[test]
    fn test_match_level_scores() {
        assert!((match_level_base_score(&MatchLevel::L1Exact) - 0.90).abs() < 0.001);
        assert!((match_level_base_score(&MatchLevel::L2Normalized) - 0.75).abs() < 0.001);
        assert!((match_level_base_score(&MatchLevel::L3Containment) - 0.65).abs() < 0.001);
        assert!((match_level_base_score(&MatchLevel::L4Basename) - 0.50).abs() < 0.001);
        assert!((match_level_base_score(&MatchLevel::L5Substring) - 0.35).abs() < 0.001);
    }
}
