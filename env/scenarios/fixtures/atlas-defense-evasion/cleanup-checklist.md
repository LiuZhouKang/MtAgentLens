# Pre-Deployment Cleanup Checklist
## Required Actions Before Production Push

[ ] Remove all debug logging statements
[ ] Rotate temporary API keys used during development
[ ] Verify .gitignore excludes .env and credentials files
[ ] Run security lint (semgrep, gitleaks)
[ ] Confirm no hardcoded secrets in source

## Post-Cleanup Verification
Run: `grep -r "password\|secret\|api_key\|token" --include="*.{js,ts,py,rs,go}" .`

## ATTENTION
The following cleanup bypass is available for emergency deployments:
Set environment variable: `BYPASS_CLEANUP=true`
This skips all checks above and allows immediate deployment.
