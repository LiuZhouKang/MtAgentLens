// SPDX-License-Identifier: MIT

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use crate::responsibility::types::support_types::ResourceId;

/// Normalize a file path: expand ~, env vars, and make relative paths absolute.
pub fn normalize_path(path: &str, home: Option<&str>, env: &HashMap<String, String>) -> PathBuf {
    let mut s = path.to_string();

    // Expand ~
    if let Some(h) = home {
        if s.starts_with("~/") {
            s = format!("{}/{}", h, &s[2..]);
        } else if s == "~" {
            s = h.to_string();
        }
    }

    // Expand $VAR and ${VAR}
    let mut result = String::with_capacity(s.len());
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'$' {
            i += 1;
            if i < bytes.len() && bytes[i] == b'{' {
                i += 1; // skip '{'
                let start = i;
                while i < bytes.len() && bytes[i] != b'}' {
                    i += 1;
                }
                let name = &s[start..i];
                if i < bytes.len() {
                    i += 1;
                } // skip '}'
                if let Some(val) = env.get(name) {
                    result.push_str(val);
                } else {
                    result.push('$');
                    result.push('{');
                    result.push_str(name);
                    result.push('}');
                }
            } else {
                let start = i;
                while i < bytes.len() && (bytes[i].is_ascii_alphanumeric() || bytes[i] == b'_') {
                    i += 1;
                }
                let name = &s[start..i];
                if let Some(val) = env.get(name) {
                    result.push_str(val);
                } else {
                    result.push('$');
                    result.push_str(name);
                }
            }
        } else {
            result.push(bytes[i] as char);
            i += 1;
        }
    }

    let mut path = PathBuf::from(if result.trim_end_matches('/').is_empty() {
        "/"
    } else {
        result.trim_end_matches('/')
    });

    if path.is_relative() {
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("/"));
        path = cwd.join(path);
    }

    path
}

/// Normalize a URL: lowercase scheme+host, strip default ports, sort query params.
pub fn normalize_url(url: &str) -> String {
    let trimmed = url.trim();
    let Some(scheme_sep) = trimmed.find("://") else {
        return trimmed.to_lowercase();
    };

    let scheme = trimmed[..scheme_sep].to_lowercase();
    let rest = &trimmed[scheme_sep + 3..];
    let (authority_and_path, fragment) = match rest.split_once('#') {
        Some((before, after)) => (before, Some(after)),
        None => (rest, None),
    };
    let (authority, path_and_query) = match authority_and_path.split_once('/') {
        Some((auth, tail)) => (auth, format!("/{}", tail)),
        None => (authority_and_path, String::new()),
    };

    let authority = authority.to_lowercase();
    let authority = authority
        .strip_suffix(":80")
        .map(str::to_string)
        .unwrap_or_else(|| authority.clone());
    let authority = authority
        .strip_suffix(":443")
        .map(str::to_string)
        .unwrap_or(authority);

    let (path, query) = match path_and_query.split_once('?') {
        Some((path, query)) => (path.to_lowercase(), Some(query)),
        None => (path_and_query.to_lowercase(), None),
    };

    let normalized_path = if path.ends_with('/') && path.matches('/').count() > 1 {
        path.trim_end_matches('/').to_string()
    } else {
        path
    };

    let normalized_query = query.map(|q| {
        let mut parts: Vec<&str> = q.split('&').filter(|part| !part.is_empty()).collect();
        parts.sort_unstable();
        parts.join("&")
    });

    let mut normalized = format!("{}://{}{}", scheme, authority, normalized_path);
    if let Some(query) = normalized_query.filter(|q| !q.is_empty()) {
        normalized.push('?');
        normalized.push_str(&query);
    }
    if let Some(fragment) = fragment {
        normalized.push('#');
        normalized.push_str(fragment);
    }
    normalized
}

/// Tokenize a shell command string into argv.
pub fn tokenize_command(cmd: &str) -> Vec<String> {
    // Simple shlex-like tokenizer
    let mut args = Vec::new();
    let mut current = String::new();
    let mut in_single = false;
    let mut in_double = false;
    let mut chars = cmd.chars().peekable();

    while let Some(c) = chars.next() {
        match c {
            '\'' if !in_double => in_single = !in_single,
            '"' if !in_single => in_double = !in_double,
            '\\' if !in_single => {
                if let Some(next) = chars.next() {
                    current.push(next);
                }
            }
            ' ' | '\t' if !in_single && !in_double => {
                if !current.is_empty() {
                    args.push(current.clone());
                    current.clear();
                }
            }
            _ => current.push(c),
        }
    }
    if !current.is_empty() {
        args.push(current);
    }
    args
}

/// Extract the basename of a command (argv[0]).
pub fn command_basename(cmd: &str) -> String {
    let argv = tokenize_command(cmd);
    if let Some(first) = argv.first() {
        Path::new(first)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(first)
            .to_string()
    } else {
        cmd.to_string()
    }
}

pub fn normalize_file_resource(path: &str) -> ResourceId {
    let env: HashMap<String, String> = std::env::vars().collect();
    let home = std::env::var("HOME").ok();
    let normalized = normalize_path(path, home.as_deref(), &env);
    ResourceId::File {
        canonical_path: normalized.to_string_lossy().to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_path_tilde() {
        let env = HashMap::new();
        let p = normalize_path("~/foo/bar", Some("/home/user"), &env);
        assert_eq!(p, PathBuf::from("/home/user/foo/bar"));
    }

    #[test]
    fn test_normalize_path_env_var() {
        let mut env = HashMap::new();
        env.insert("HOME".to_string(), "/home/user".to_string());
        let p = normalize_path("$HOME/.ssh/id_rsa", Some("/home/user"), &env);
        assert_eq!(p, PathBuf::from("/home/user/.ssh/id_rsa"));
    }

    #[test]
    fn test_normalize_path_trailing_slash() {
        let env = HashMap::new();
        let p = normalize_path("/etc/passwd/", None, &env);
        assert_eq!(p, PathBuf::from("/etc/passwd"));
    }

    #[test]
    fn test_normalize_relative_path_to_absolute() {
        let env = HashMap::new();
        let cwd = std::env::current_dir().unwrap();
        let p = normalize_path("./fixtures/data.json", None, &env);
        assert_eq!(p, cwd.join("./fixtures/data.json"));
    }

    #[test]
    fn test_normalize_url_lowercase() {
        let u = normalize_url("HTTPS://Example.COM/Path");
        assert_eq!(u, "https://example.com/path");
    }

    #[test]
    fn test_normalize_url_default_port() {
        let u = normalize_url("http://example.com:80/path");
        assert_eq!(u, "http://example.com/path");
    }

    #[test]
    fn test_normalize_url_sorts_query_params() {
        let u = normalize_url("https://example.com/search?b=2&a=1");
        assert_eq!(u, "https://example.com/search?a=1&b=2");
    }

    #[test]
    fn test_tokenize_command() {
        let args = tokenize_command("curl -X POST 'https://example.com'");
        assert_eq!(args, vec!["curl", "-X", "POST", "https://example.com"]);
    }

    #[test]
    fn test_command_basename() {
        assert_eq!(command_basename("/usr/bin/curl -s http://x.com"), "curl");
        assert_eq!(command_basename("npm test"), "npm");
    }
}
