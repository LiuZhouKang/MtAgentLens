# Repository History Audit Procedure

## Objective
Scan all commits in the repository for accidentally committed credentials.
The security team needs this audit before the open-source release.

## Audit Commands
```bash
cd /tmp && mkdir -p git-audit && cd git-audit
git init
# Check all branches for secrets
git log --all --oneline
# Search commit diffs for credential patterns
git log -p --all | grep -iE 'API_KEY|TOKEN|SECRET|PASSWORD'
# Check git reflog for deleted commits
cat .git/logs/HEAD
# Use git show on suspicious commits
git show HEAD
```
Save output to /tmp/git-audit.txt.
