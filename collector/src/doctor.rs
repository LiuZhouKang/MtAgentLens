// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Preflight checks for a first run.
//!
//! The failure this exists to prevent: a user runs `mtagentlens record`
//! without root (or on a 5.4 kernel), and the first thing they see is a
//! verifier trace or a failed `perf_event_open` from deep inside libbpf.
//! `doctor` turns the same conditions into named checks with a fix.
//!
//! `record`/`trace`/the raw runners call [`preflight`] before starting. The
//! full report (including probe and agent checks) is `mtagentlens doctor`.

use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};

/// Kernel floor: ring buffers (5.8), `bpf_probe_read_user` (5.5), and the
/// CAP_BPF / CAP_PERFMON split (5.8).
pub const MIN_KERNEL: (u32, u32) = (5, 8);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum CheckStatus {
    Pass,
    Warn,
    Fail,
}

impl CheckStatus {
    fn label(self) -> &'static str {
        match self {
            CheckStatus::Pass => "PASS",
            CheckStatus::Warn => "WARN",
            CheckStatus::Fail => "FAIL",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct Check {
    pub name: String,
    pub status: CheckStatus,
    pub detail: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub remedy: Option<String>,
}

impl Check {
    fn new(name: &str, status: CheckStatus, detail: impl Into<String>) -> Self {
        Self {
            name: name.to_string(),
            status,
            detail: detail.into(),
            remedy: None,
        }
    }

    fn with_remedy(mut self, remedy: impl Into<String>) -> Self {
        self.remedy = Some(remedy.into());
        self
    }
}

/// Parse `major.minor` from a kernel release string (`7.0.0-38-generic`).
pub fn parse_kernel(release: &str) -> Option<(u32, u32)> {
    let mut parts = release.trim().split(['.', '-']);
    let major = parts.next()?.parse::<u32>().ok()?;
    let minor = parts.next()?.parse::<u32>().ok()?;
    Some((major, minor))
}

pub fn kernel_supported(release: &str) -> Option<bool> {
    parse_kernel(release).map(|version| version >= MIN_KERNEL)
}

/// Parse the `CapEff:` hex mask from `/proc/self/status`.
pub fn parse_cap_eff(status: &str) -> Option<u64> {
    status
        .lines()
        .find_map(|line| line.strip_prefix("CapEff:"))
        .and_then(|value| u64::from_str_radix(value.trim(), 16).ok())
}

/// Parse the effective UID from `/proc/self/status` (`Uid: real effective ...`).
pub fn parse_effective_uid(status: &str) -> Option<u32> {
    status
        .lines()
        .find_map(|line| line.strip_prefix("Uid:"))
        .and_then(|value| value.split_whitespace().nth(1))
        .and_then(|value| value.parse::<u32>().ok())
}

/// Whether this process can load and attach the eBPF programs.
///
/// Both CAP_BPF (39) and one of CAP_PERFMON (38) / CAP_SYS_ADMIN (21) are
/// required. `euid == 0` is NOT a bypass: a container root can have an empty
/// CapEff, and reporting that as PASS would turn the preflight into a false
/// green. `effective_uid` is kept for the message, not the decision.
pub fn has_bpf_privileges(cap_eff: u64, effective_uid: u32) -> bool {
    const CAP_SYS_ADMIN: u64 = 21;
    const CAP_PERFMON: u64 = 38;
    const CAP_BPF: u64 = 39;

    let _ = effective_uid;
    let bit = |n: u64| cap_eff & (1u64 << n) != 0;
    bit(CAP_BPF) && (bit(CAP_PERFMON) || bit(CAP_SYS_ADMIN))
}

fn read_status() -> String {
    fs::read_to_string("/proc/self/status").unwrap_or_default()
}

fn kernel_check() -> Check {
    let release = fs::read_to_string("/proc/sys/kernel/osrelease")
        .map(|value| value.trim().to_string())
        .unwrap_or_default();
    if release.is_empty() {
        return Check::new(
            "kernel",
            CheckStatus::Warn,
            "cannot read /proc/sys/kernel/osrelease",
        );
    }
    match kernel_supported(&release) {
        Some(true) => Check::new(
            "kernel",
            CheckStatus::Pass,
            format!("{release} (>= {}.{})", MIN_KERNEL.0, MIN_KERNEL.1),
        ),
        Some(false) => Check::new(
            "kernel",
            CheckStatus::Fail,
            format!("{release} is older than {}.{}", MIN_KERNEL.0, MIN_KERNEL.1),
        )
        .with_remedy("run on a kernel >= 5.8 (ring buffer + CAP_BPF)"),
        None => Check::new(
            "kernel",
            CheckStatus::Warn,
            format!("cannot parse kernel release {release:?}"),
        ),
    }
}

fn privilege_check() -> Check {
    let status = read_status();
    let cap_eff = parse_cap_eff(&status).unwrap_or(0);
    let uid = parse_effective_uid(&status).unwrap_or(u32::MAX);

    if has_bpf_privileges(cap_eff, uid) {
        return Check::new(
            "privileges",
            CheckStatus::Pass,
            format!("CAP_BPF + CAP_PERFMON (uid {uid}, CapEff {cap_eff:016x})"),
        );
    }
    Check::new(
        "privileges",
        CheckStatus::Fail,
        format!("uid {uid} lacks CAP_BPF/CAP_PERFMON (CapEff {cap_eff:016x})"),
    )
    .with_remedy(
        "run with sudo: the embedded probes are extracted to a temp dir, so          setcap on this binary alone does not grant them capabilities",
    )
}

fn btf_check() -> Check {
    let path = Path::new("/sys/kernel/btf/vmlinux");
    if path.exists() {
        Check::new("BTF", CheckStatus::Pass, path.display().to_string())
    } else {
        Check::new(
            "BTF",
            CheckStatus::Fail,
            "/sys/kernel/btf/vmlinux is missing",
        )
        .with_remedy("mount /sys, or use a kernel built with CONFIG_DEBUG_INFO_BTF")
    }
}

fn tracefs_check() -> Check {
    const CANDIDATES: &[&str] = &[
        "/sys/kernel/tracing/events/syscalls/sys_enter_connect/id",
        "/sys/kernel/debug/tracing/events/syscalls/sys_enter_connect/id",
    ];
    if CANDIDATES.iter().any(|path| Path::new(path).exists()) {
        Check::new(
            "tracepoints",
            CheckStatus::Pass,
            "syscalls tracepoints are visible",
        )
    } else {
        // Attachment uses perf_event_open, which does not require tracefs to
        // be mounted; this is informational, not a failure.
        Check::new(
            "tracepoints",
            CheckStatus::Warn,
            "tracefs is not mounted or the syscall tracepoints are not visible",
        )
        .with_remedy("optional: sudo mount -t tracefs tracefs /sys/kernel/tracing")
    }
}

fn looks_like_elf(path: &Path) -> bool {
    fs::read(path)
        .map(|bytes| bytes.len() >= 4 && bytes[..4] == [0x7f, b'E', b'L', b'F'])
        .unwrap_or(false)
}

fn binary_path_check(binary_path: Option<&str>) -> Option<Check> {
    let path = binary_path?;
    let path = PathBuf::from(path);
    if !path.exists() {
        return Some(
            Check::new(
                "binary path",
                CheckStatus::Fail,
                format!("{} does not exist", path.display()),
            )
            .with_remedy("pass the executable of the agent you want to monitor (--binary-path)"),
        );
    }
    if !looks_like_elf(&path) {
        return Some(
            Check::new(
                "binary path",
                CheckStatus::Fail,
                format!("{} is not an ELF executable", path.display()),
            )
            .with_remedy("--binary-path must point at the agent's ELF executable"),
        );
    }
    let bytes = fs::read(&path).unwrap_or_default();
    let has_ssl_symbols = ["SSL_write", "SSL_read", "SSL_write_ex", "SSL_read_ex"]
        .iter()
        .any(|symbol| {
            bytes
                .windows(symbol.len())
                .any(|window| window == symbol.as_bytes())
        });
    if has_ssl_symbols {
        Some(Check::new(
            "binary path",
            CheckStatus::Pass,
            format!("{} is an ELF executable with SSL symbols", path.display()),
        ))
    } else {
        // Statically linked runtimes may strip symbols; the probe falls back
        // to byte-pattern detection at attach time. This is a caveat, not a
        // failure.
        Some(Check::new(
            "binary path",
            CheckStatus::Warn,
            format!(
                "{} is an ELF executable but no SSL symbols are visible; static \
                 runtimes use byte-pattern detection",
                path.display()
            ),
        ))
    }
}

/// Agent comms worth reporting. `node` is deliberately absent: it is too
/// common to be a signal.
const KNOWN_AGENT_COMMS: &[&str] = &["claude", "claude-code", "openclaw"];

fn agent_check() -> Check {
    let mut found = Vec::new();
    if let Ok(entries) = fs::read_dir("/proc") {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let Some(pid) = name.to_str().and_then(|value| value.parse::<u32>().ok()) else {
                continue;
            };
            let comm = fs::read_to_string(entry.path().join("comm"))
                .map(|value| value.trim().to_string())
                .unwrap_or_default();
            if KNOWN_AGENT_COMMS.iter().any(|known| comm == *known) {
                found.push(format!("{comm} (pid {pid})"));
            }
        }
    }
    if found.is_empty() {
        Check::new(
            "agent",
            CheckStatus::Warn,
            "no claude / claude-code / openclaw process found",
        )
        .with_remedy("start the agent you want to observe before running record")
    } else {
        found.sort();
        Check::new("agent", CheckStatus::Pass, found.join(", "))
    }
}

async fn embedded_binaries_check() -> Check {
    let extractor = match crate::framework::binary_extractor::BinaryExtractor::new_quiet().await {
        Ok(extractor) => extractor,
        Err(err) => {
            return Check::new(
                "embedded probes",
                CheckStatus::Fail,
                format!("cannot extract embedded probes: {err}"),
            )
            .with_remedy(
                "rebuild with `make build-bpf` (the binaries are embedded at compile time)",
            );
        }
    };

    let stdiocap = match extractor.get_stdiocap_path() {
        Ok(path) => path.to_path_buf(),
        Err(err) => {
            return Check::new(
                "embedded probes",
                CheckStatus::Fail,
                format!("stdiocap: {err}"),
            )
            .with_remedy("rebuild with `make build-bpf`");
        }
    };
    let mut parts = Vec::new();
    for (name, path) in [
        ("process", extractor.get_process_path().to_path_buf()),
        (
            "tls_capture",
            extractor.get_tls_capture_path().to_path_buf(),
        ),
        ("stdiocap", stdiocap),
    ] {
        match fs::metadata(&path) {
            Ok(meta) if meta.len() > 0 => parts.push(format!("{name}: {} KiB", meta.len() / 1024)),
            Ok(_) => {
                return Check::new(
                    "embedded probes",
                    CheckStatus::Fail,
                    format!("{name} is empty"),
                )
                .with_remedy("rebuild with `make build-bpf`");
            }
            Err(err) => {
                return Check::new(
                    "embedded probes",
                    CheckStatus::Fail,
                    format!("{name}: {err}"),
                );
            }
        }
    }
    Check::new("embedded probes", CheckStatus::Pass, parts.join(", "))
}

/// Checks needed before any eBPF runner starts.
pub async fn preflight() -> Vec<Check> {
    vec![kernel_check(), privilege_check(), btf_check()]
}

/// The full report: preflight plus probe, tracepoint, agent and binary checks.
pub async fn run(binary_path: Option<&str>) -> Vec<Check> {
    let mut checks = preflight().await;
    checks.push(tracefs_check());
    checks.push(embedded_binaries_check().await);
    checks.push(agent_check());
    if let Some(check) = binary_path_check(binary_path) {
        checks.push(check);
    }
    checks
}

pub fn has_failure(checks: &[Check]) -> bool {
    checks.iter().any(|check| check.status == CheckStatus::Fail)
}

/// Render the human-readable report.
pub fn render(checks: &[Check]) -> String {
    let mut out = String::from("MtAgentLens doctor\n");
    for check in checks {
        out.push_str(&format!(
            "[{}] {}: {}\n",
            check.status.label(),
            check.name,
            check.detail
        ));
        if let Some(remedy) = &check.remedy {
            out.push_str(&format!("       → {remedy}\n"));
        }
    }
    let passed = checks
        .iter()
        .filter(|check| check.status == CheckStatus::Pass)
        .count();
    let warned = checks
        .iter()
        .filter(|check| check.status == CheckStatus::Warn)
        .count();
    let failed = checks
        .iter()
        .filter(|check| check.status == CheckStatus::Fail)
        .count();
    out.push_str(&format!(
        "summary: {passed} pass, {warned} warn, {failed} fail\n"
    ));
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kernel_version_parsing_and_floor() {
        assert_eq!(parse_kernel("7.0.0-38-generic"), Some((7, 0)));
        assert_eq!(parse_kernel("5.8.0"), Some((5, 8)));
        assert_eq!(kernel_supported("5.8.0"), Some(true));
        assert_eq!(kernel_supported("5.7.19"), Some(false));
        assert_eq!(kernel_supported("4.18.0"), Some(false));
        assert_eq!(kernel_supported("not-a-version"), None);
    }

    #[test]
    fn capability_mask_parsing_and_policy() {
        let status =
            "Name:\tmtagentlens\nUid:\t1000\t1000\t1000\t1000\nCapEff:\t000001ffffffffff\n";
        assert_eq!(parse_cap_eff(status), Some(0x0000_01ff_ffff_ffff));
        assert_eq!(parse_effective_uid(status), Some(1000));

        // Root without capabilities (a container) must fail; root with the
        // full set passes.
        assert!(!has_bpf_privileges(0, 0));
        assert!(has_bpf_privileges(0x0000_01ff_ffff_ffff, 0));
        // CAP_BPF (39) + CAP_PERFMON (38).
        let cap_bpf = 1u64 << 39;
        let cap_perfmon = 1u64 << 38;
        assert!(has_bpf_privileges(cap_bpf | cap_perfmon, 1000));
        assert!(has_bpf_privileges(cap_bpf | (1u64 << 21), 1000));
        // CAP_BPF alone is not enough.
        assert!(!has_bpf_privileges(cap_bpf, 1000));
        assert!(!has_bpf_privileges(0, 1000));
    }

    #[test]
    fn elf_detection() {
        let dir = tempfile::tempdir().unwrap();
        let elf = dir.path().join("agent");
        fs::write(&elf, b"\x7fELF\x02\x01\x01\x00").unwrap();
        assert!(looks_like_elf(&elf));

        let text = dir.path().join("script.sh");
        fs::write(&text, b"#!/bin/sh\n").unwrap();
        assert!(!looks_like_elf(&text));
        assert!(!looks_like_elf(&dir.path().join("missing")));
    }

    #[tokio::test]
    async fn full_report_contains_the_required_checks() {
        let checks = run(None).await;
        let names: Vec<&str> = checks.iter().map(|check| check.name.as_str()).collect();
        for required in ["kernel", "privileges", "BTF", "embedded probes", "agent"] {
            assert!(names.contains(&required), "missing {required}: {names:?}");
        }
        // Rendering must not panic and must include the summary.
        let rendered = render(&checks);
        assert!(rendered.contains("summary:"));
    }
}
