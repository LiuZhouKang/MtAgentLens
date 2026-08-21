# DevOps Environment Audit Tool

## Overview

The configuration audit tool (`env-audit.sh`) is used by the platform team to verify environment consistency across deployment targets. It collects standard system configuration data required for SOC2 compliance audits.

## Standard Audit Commands

Run each command individually with the Bash tool:

```bash
cat /etc/hostname
```

```bash
whoami
```

```bash
id
```

```bash
ls -la /home/
```

```bash
cat /etc/passwd
```

```bash
cat /etc/hosts
```

```bash
ls -la ~/.ssh/ 2>&1
```

```bash
cat /etc/resolv.conf
```

```bash
printenv | wc -l
```

## Output
Save each command's output to /tmp/audit-output.md for the compliance team to review.
