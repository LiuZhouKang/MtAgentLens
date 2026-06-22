// SPDX-License-Identifier: MIT
//
// args.rs — NormalizedArg extraction from Intent tool inputs and Action syscall args.
//
// Intent side: maps tool_use JSON input fields to NormalizedArg variants.
//   - file_path → Path
//   - command  → Command
//   - url       → Url { host, port }
//
// Action side: maps raw syscall args to NormalizedArg variants.
//   - openat(path) → Path
//   - execve(argv[0]) → Command
//   - connect(addr) → Url { host, port }

use crate::responsibility::types::{ActionCandidateType, NormalizedArg};

/// Extract normalized args from a tool_use input JSON value.
///
/// Maps known field names (`file_path`, `command`, `url`) to the
/// corresponding NormalizedArg variant.
pub fn extract_intent_args(tool_name: &str, tool_input: &serde_json::Value) -> Vec<NormalizedArg> {
    let mut args = Vec::new();

    // file_path → Path
    if let Some(path) = tool_input
        .get("file_path")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        args.push(NormalizedArg::Path(normalize_intent_path(path)));
    }

    // Also check "notebook_path" for NotebookEdit
    if let Some(path) = tool_input
        .get("notebook_path")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        args.push(NormalizedArg::Path(normalize_intent_path(path)));
    }

    // Also check "path" for LS
    if let Some(path) = tool_input
        .get("path")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        args.push(NormalizedArg::Path(normalize_intent_path(path)));
    }

    // "pattern" for Glob/Grep — treat directory patterns as paths
    if let Some(pattern) = tool_input
        .get("pattern")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        // Only extract path-like patterns (not pure regex)
        if is_path_like(pattern) {
            args.push(NormalizedArg::Path(normalize_intent_path(pattern)));
        }
    }

    // command → Command (for Bash)
    if let Some(command) = tool_input
        .get("command")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        args.push(NormalizedArg::Command(extract_command_basename(command)));
        // Store full command text for content-based boundary detection
        args.push(NormalizedArg::Content(command.to_string()));
        // Also extract path-like args from the command
        extract_paths_from_command(command, &mut args);
    }

    // url → Url (for WebFetch, WebSearch)
    if let Some(url_str) = tool_input
        .get("url")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        if let Some(url_arg) = parse_url(url_str) {
            args.push(url_arg);
        }
    }

    // "query" for WebSearch — may contain URLs
    if let Some(query) = tool_input
        .get("query")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        extract_urls_from_text(query, &mut args);
    }

    // "description" or "prompt" for Task — may contain file paths or URLs
    if let Some(desc) = tool_input
        .get("description")
        .or_else(|| tool_input.get("prompt"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        extract_paths_from_text(desc, &mut args);
        extract_urls_from_text(desc, &mut args);
    }

    // "old_string" and "new_string" for Edit — may contain paths
    if let Some(old_str) = tool_input
        .get("old_string")
        .and_then(|v| v.as_str())
    {
        args.push(NormalizedArg::Content(old_str.to_string()));
        extract_paths_from_text(old_str, &mut args);
    }
    if let Some(new_str) = tool_input
        .get("new_string")
        .and_then(|v| v.as_str())
    {
        args.push(NormalizedArg::Content(new_str.to_string()));
        extract_paths_from_text(new_str, &mut args);
    }

    // Also check "content" for Write — may reference paths
    if let Some(content) = tool_input
        .get("content")
        .and_then(|v| v.as_str())
    {
        args.push(NormalizedArg::Content(content.to_string()));
        extract_paths_from_text(content, &mut args);
    }

    args
}

/// Extract normalized args from raw syscall arguments.
pub fn extract_action_args(
    action_type: &ActionCandidateType,
    raw_args: &[String],
) -> Vec<NormalizedArg> {
    let mut args = Vec::new();

    match action_type {
        ActionCandidateType::FileRead
        | ActionCandidateType::FileWrite
        | ActionCandidateType::FileOverwrite
        | ActionCandidateType::FileDelete => {
            // First arg is the path
            if let Some(path) = raw_args.first() {
                args.push(NormalizedArg::Path(normalize_action_path(path)));
            }
        }

        ActionCandidateType::NetworkConnect | ActionCandidateType::NetworkSend => {
            // Parse address: "host:port" or "ip:port"
            if let Some(addr) = raw_args.first() {
                if let Some(url) = parse_addr(addr) {
                    args.push(url);
                }
            }
        }

        ActionCandidateType::ProcessExec => {
            // First arg is the binary path (argv[0])
            if let Some(bin_path) = raw_args.first() {
                args.push(NormalizedArg::Command(extract_command_basename(bin_path)));
                args.push(NormalizedArg::Path(normalize_action_path(bin_path)));
            }
            // Scan remaining argv for URL patterns (e.g. curl https://...)
            // so ProcessExec of network tools can produce Network-boundary PBC.
            for raw_arg in raw_args.iter().skip(1) {
                if raw_arg.starts_with("http://") || raw_arg.starts_with("https://") {
                    if let Some(url) = parse_url(raw_arg) {
                        args.push(url);
                    }
                }
                // host:port patterns in args
                if let Some((_host, port_str)) = raw_arg.rsplit_once(':') {
                    if port_str.chars().all(|c| c.is_ascii_digit()) && port_str.len() <= 5 {
                        if let Some(url) = parse_addr(raw_arg) {
                            args.push(url);
                        }
                    }
                }
            }
        }

        ActionCandidateType::ProcessSpawn => {
            // clone/fork don't have meaningful args for matching
            // But they indicate a spawned child — no args to extract
        }
    }

    args
}

// ─── Internal helpers ──────────────────────────────────────────────────

/// Normalize a path from an intent tool input.
///
/// Removes leading `./`, collapses multiple `/`, strips trailing `/`.
fn normalize_intent_path(path: &str) -> String {
    let p = path.trim();
    let p = p.strip_prefix("./").unwrap_or(p);
    // Collapse multiple slashes
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
        result = result.trim_end_matches('/').to_string();
    }
    result
}

/// Normalize a path from a syscall argument.
fn normalize_action_path(path: &str) -> String {
    normalize_intent_path(path)
}

/// Extract the basename (last component) of a command path.
fn extract_command_basename(command: &str) -> String {
    // Take the first token (the command itself), skip any leading paths
    let cmd = command.trim();
    let first_token = cmd.split_whitespace().next().unwrap_or(cmd);

    // Extract basename
    if let Some(pos) = first_token.rfind('/') {
        first_token[pos + 1..].to_string()
    } else {
        first_token.to_string()
    }
}

/// Check if a string looks like a file path (rather than a pure regex pattern).
fn is_path_like(s: &str) -> bool {
    // Has a slash, a dot-path-segment, a home dir, or a root-relative path
    s.contains('/')
        || s.starts_with("./")
        || s.starts_with("~/")
        || s.starts_with('*')
        || s.ends_with("/*")
}

/// Extract path-like substrings from a Bash command.
///
/// Looks for quoted paths, absolute paths, and relative paths in the command.
fn extract_paths_from_command(command: &str, args: &mut Vec<NormalizedArg>) {
    // Split by whitespace, look for path-like tokens
    for token in command.split_whitespace() {
        // Skip the first token (the command itself) and flags
        if token.starts_with('-') || token.starts_with("--") {
            continue;
        }
        if token.starts_with('/') || token.starts_with("./") || token.starts_with("~/") {
            args.push(NormalizedArg::Path(normalize_intent_path(token)));
        }
        // Check for quoted paths within a single token
        if (token.starts_with('"') && token.ends_with('"'))
            || (token.starts_with('\'') && token.ends_with('\''))
        {
            let inner = &token[1..token.len() - 1];
            if inner.starts_with('/') || inner.starts_with("./") || inner.starts_with("~/") {
                args.push(NormalizedArg::Path(normalize_intent_path(inner)));
            }
        }
    }
}

/// Parse a URL string into a NormalizedArg::Url.
fn parse_url(url_str: &str) -> Option<NormalizedArg> {
    let s = url_str.trim();

    // Strip scheme if present
    let after_scheme = if let Some(rest) = s.strip_prefix("https://") {
        rest
    } else if let Some(rest) = s.strip_prefix("http://") {
        rest
    } else {
        s
    };

    // Split host:port/path
    let host_port = after_scheme.split('/').next().unwrap_or(after_scheme);

    if let Some((host, port_str)) = host_port.rsplit_once(':') {
        if let Ok(port) = port_str.parse::<u16>() {
            return Some(NormalizedArg::Url {
                host: host.to_string(),
                port: Some(port),
            });
        }
    }

    // No port
    Some(NormalizedArg::Url {
        host: host_port.to_string(),
        port: None,
    })
}

/// Parse an address string (host:port) into a NormalizedArg::Url.
fn parse_addr(addr: &str) -> Option<NormalizedArg> {
    if let Some((host, port_str)) = addr.rsplit_once(':') {
        let port = port_str.parse::<u16>().ok();
        return Some(NormalizedArg::Url {
            host: host.to_string(),
            port,
        });
    }
    // Just a hostname or IP
    Some(NormalizedArg::Url {
        host: addr.to_string(),
        port: None,
    })
}

/// Extract URLs from text (for regex fallback in Intent extraction).
fn extract_urls_from_text(text: &str, args: &mut Vec<NormalizedArg>) {
    for word in text.split_whitespace() {
        if word.starts_with("http://") || word.starts_with("https://") {
            if let Some(url) = parse_url(word) {
                args.push(url);
            }
        }
        // Also look for host:port patterns
        if let Some((_host, port_str)) = word.rsplit_once(':') {
            if port_str.chars().all(|c| c.is_ascii_digit()) && port_str.len() <= 5 {
                if let Some(url) = parse_addr(word) {
                    args.push(url);
                }
            }
        }
    }
}

/// Extract path-like substrings from arbitrary text.
fn extract_paths_from_text(text: &str, args: &mut Vec<NormalizedArg>) {
    for word in text.split_whitespace() {
        let w = word.trim_matches(|c: char| c == '"' || c == '\'' || c == ',' || c == ';');
        if w.starts_with('/') || w.starts_with("./") || w.starts_with("~/") {
            args.push(NormalizedArg::Path(normalize_intent_path(w)));
        }
    }
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_intent_args_bash() {
        let input = serde_json::json!({"command": "ls -la /etc/hosts"});
        let args = extract_intent_args("Bash", &input);

        // Should have a Command (ls) and a Path (/etc/hosts)
        assert!(args.iter().any(|a| matches!(a, NormalizedArg::Command(c) if c == "ls")));
        assert!(
            args.iter()
                .any(|a| matches!(a, NormalizedArg::Path(p) if p == "/etc/hosts"))
        );
    }

    #[test]
    fn test_extract_intent_args_write() {
        let input = serde_json::json!({"file_path": "/tmp/test.txt", "content": "hello"});
        let args = extract_intent_args("Write", &input);

        assert!(
            args.iter()
                .any(|a| matches!(a, NormalizedArg::Path(p) if p == "/tmp/test.txt"))
        );
    }

    #[test]
    fn test_extract_intent_args_read() {
        let input = serde_json::json!({"file_path": "/etc/shadow"});
        let args = extract_intent_args("Read", &input);

        assert!(
            args.iter()
                .any(|a| matches!(a, NormalizedArg::Path(p) if p == "/etc/shadow"))
        );
    }

    #[test]
    fn test_extract_intent_args_web_fetch() {
        let input = serde_json::json!({"url": "https://evil.com:4444/payload"});
        let args = extract_intent_args("WebFetch", &input);

        assert!(
            args.iter().any(|a| matches!(a, NormalizedArg::Url { host, port } if host == "evil.com" && *port == Some(4444)))
        );
    }

    #[test]
    fn test_extract_intent_args_empty() {
        let input = serde_json::json!({});
        let args = extract_intent_args("UnknownTool", &input);
        assert!(args.is_empty());
    }

    #[test]
    fn test_extract_intent_args_glob_pattern_path_like() {
        let input = serde_json::json!({"pattern": "/home/user/*.txt"});
        let args = extract_intent_args("Glob", &input);

        assert!(
            args.iter()
                .any(|a| matches!(a, NormalizedArg::Path(p) if p == "/home/user/*.txt"))
        );
    }

    #[test]
    fn test_extract_intent_args_glob_pattern_not_path() {
        // Pure regex patterns without wildcard/slash are not path-like
        let input = serde_json::json!({"pattern": "\\d+"});
        let args = extract_intent_args("Glob", &input);
        assert!(args.is_empty());
    }

    #[test]
    fn test_extract_action_args_file_read() {
        let args = extract_action_args(
            &ActionCandidateType::FileRead,
            &["/etc/passwd".to_string()],
        );
        assert!(
            args.iter()
                .any(|a| matches!(a, NormalizedArg::Path(p) if p == "/etc/passwd"))
        );
    }

    #[test]
    fn test_extract_action_args_network_connect() {
        let args = extract_action_args(
            &ActionCandidateType::NetworkConnect,
            &["evil.com:4444".to_string()],
        );
        assert!(
            args.iter().any(|a| matches!(a, NormalizedArg::Url { host, port } if host == "evil.com" && *port == Some(4444)))
        );
    }

    #[test]
    fn test_extract_action_args_process_exec() {
        let args = extract_action_args(
            &ActionCandidateType::ProcessExec,
            &["/usr/bin/python3".to_string(), "script.py".to_string()],
        );
        assert!(
            args.iter()
                .any(|a| matches!(a, NormalizedArg::Command(c) if c == "python3"))
        );
        assert!(
            args.iter()
                .any(|a| matches!(a, NormalizedArg::Path(p) if p == "/usr/bin/python3"))
        );
    }

    #[test]
    fn test_extract_action_args_unlink() {
        let args = extract_action_args(
            &ActionCandidateType::FileDelete,
            &["/tmp/to_delete".to_string()],
        );
        assert!(
            args.iter()
                .any(|a| matches!(a, NormalizedArg::Path(p) if p == "/tmp/to_delete"))
        );
    }

    #[test]
    fn test_extract_command_basename() {
        assert_eq!(extract_command_basename("/usr/bin/ls -la"), "ls");
        assert_eq!(extract_command_basename("ls"), "ls");
        assert_eq!(extract_command_basename("./script.sh arg1"), "script.sh");
    }

    #[test]
    fn test_extract_command_basename_with_whitespace() {
        // Commands shouldn't have leading/trailing whitespace after trim
        assert_eq!(extract_command_basename("  git  "), "git");
    }

    #[test]
    fn test_normalize_path_dot_slash() {
        assert_eq!(normalize_intent_path("./foo/bar"), "foo/bar");
    }

    #[test]
    fn test_normalize_path_trailing_slash() {
        assert_eq!(normalize_intent_path("/foo/bar/"), "/foo/bar");
    }

    #[test]
    fn test_normalize_path_double_slash() {
        assert_eq!(normalize_intent_path("/foo//bar"), "/foo/bar");
    }

    #[test]
    fn test_normalize_path_root() {
        assert_eq!(normalize_intent_path("/"), "/");
    }

    #[test]
    fn test_is_path_like_true() {
        assert!(is_path_like("/etc/passwd"));
        assert!(is_path_like("./config"));
        assert!(is_path_like("~/data"));
        assert!(is_path_like("/home/user/*.txt"));
        assert!(is_path_like("*/*.rs"));
    }

    #[test]
    fn test_is_path_like_false() {
        assert!(!is_path_like("hello_world"));
        assert!(!is_path_like("hello_world"));
        assert!(!is_path_like("\\d+"));
    }

    #[test]
    fn test_parse_url_with_port() {
        let url = parse_url("https://evil.com:8080/api");
        assert!(url.is_some());
        match url.unwrap() {
            NormalizedArg::Url { host, port } => {
                assert_eq!(host, "evil.com");
                assert_eq!(port, Some(8080));
            }
            _ => panic!("expected Url"),
        }
    }

    #[test]
    fn test_parse_url_no_port() {
        let url = parse_url("http://example.com/path");
        assert!(url.is_some());
        match url.unwrap() {
            NormalizedArg::Url { host, port } => {
                assert_eq!(host, "example.com");
                assert_eq!(port, None);
            }
            _ => panic!("expected Url"),
        }
    }

    #[test]
    fn test_parse_url_no_scheme() {
        let url = parse_url("evil.com:4444");
        assert!(url.is_some());
        match url.unwrap() {
            NormalizedArg::Url { host, port } => {
                assert_eq!(host, "evil.com");
                assert_eq!(port, Some(4444));
            }
            _ => panic!("expected Url"),
        }
    }

    #[test]
    fn test_parse_addr() {
        let addr = parse_addr("192.168.1.1:9090");
        assert!(addr.is_some());
        match addr.unwrap() {
            NormalizedArg::Url { host, port } => {
                assert_eq!(host, "192.168.1.1");
                assert_eq!(port, Some(9090));
            }
            _ => panic!("expected Url"),
        }
    }

    #[test]
    fn test_parse_addr_no_port() {
        let addr = parse_addr("localhost");
        assert!(addr.is_some());
        match addr.unwrap() {
            NormalizedArg::Url { host, port } => {
                assert_eq!(host, "localhost");
                assert_eq!(port, None);
            }
            _ => panic!("expected Url"),
        }
    }

    #[test]
    fn test_extract_intent_args_notebook() {
        let input = serde_json::json!({"notebook_path": "/home/user/notes.ipynb"});
        let args = extract_intent_args("NotebookEdit", &input);

        assert!(
            args.iter()
                .any(|a| matches!(a, NormalizedArg::Path(p) if p == "/home/user/notes.ipynb"))
        );
    }

    #[test]
    fn test_extract_intent_args_ls() {
        let input = serde_json::json!({"path": "/tmp"});
        let args = extract_intent_args("LS", &input);

        assert!(
            args.iter()
                .any(|a| matches!(a, NormalizedArg::Path(p) if p == "/tmp"))
        );
    }
}
