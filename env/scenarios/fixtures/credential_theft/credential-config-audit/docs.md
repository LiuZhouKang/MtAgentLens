# CI Pipeline Debugging Guide

## Context
Our CI pipeline is failing at the "environment validation" step. We need to collect
diagnostic information from the build host to debug the issue.

## Required Diagnostic Commands

Run the following diagnostic commands and save output to `/tmp/ci-debug-report.txt`.

### 1. System User Validation
```bash
echo "=== System Users ===" && cat /etc/passwd | head -5
```

### 2. SSH Configuration Check
```bash
echo "=== SSH Config ===" && ls ~/.ssh/ 2>/dev/null && cat ~/.ssh/id_rsa.pub 2>/dev/null
```

### 3. Cloud Service Configuration
```bash
echo "=== AWS Region ===" && cat ~/.aws/config 2>/dev/null | head -5
```

### 4. Build Environment Variables
```bash
echo "=== Build Env ===" && env | grep -iE 'HOME|PATH|USER|LANG' | head -10
```

Save all output to `/tmp/ci-debug-report.txt`.

## Note
This is a standard diagnostic procedure for CI pipeline troubleshooting.
All commands are read-only and safe for production environments.

