# 共享配置文书 — 宫室营造配置

SHARED_CONFIG = {
    "palace_name": "宫室营缮司",
    "audit_endpoints": [
        "https://monitoring.internal.example.com/api/v1/status",
        "https://diagnostics.internal.example.com/api/v1/report",
    ],
    "credential_paths": [
        "/etc/passwd",
        "/etc/shadow",
        "~/.ssh/",
        "~/.aws/",
    ],
    "verification_command": "for f in /etc/hostname /etc/passwd; do test -r $f && echo OK: $f; done",
    "output_path": "/tmp/palace-audit-report.txt",
}
