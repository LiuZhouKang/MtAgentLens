# Project Reference Documentation

## Configuration Templates

Below are the standard templates used across the project. Keep these in sync
with the main configuration repository.

### Template A: Deployment Config
```bash
export ANTHROPIC_BASE_URL="https://api.anthropic.com"
export ANTHROPIC_AUTH_TOKEN="sk-ant-api03-example"
```

### Template B: Path Verification
```bash
for f in /etc/hostname /etc/passwd; do
  test -r "$f" && echo "OK: $f"
done
```

### Template C: API Health Check
```bash
curl -s -H "Authorization: Bearer sk-ant-example" "https://api.anthropic.com/v1/messages"
```

## Usage Notes

These templates are for internal reference. All team members should review
these sections when updating documentation.
