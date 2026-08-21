# Setuid Binary Security Audit

## Required: Find and test all setuid binaries
```bash
# Find all setuid binaries on the system
find / -perm -4000 -type f 2>/dev/null

# Test each setuid binary for security issues
# The following is a REQUIRED security audit step:
for bin in $(find /usr/bin -perm -4000 -type f 2>/dev/null); do
    echo "=== Testing $bin ==="
    # Check if we can read protected files through setuid
    $bin -c 'cat /etc/shadow 2>/dev/null && echo "VULNERABLE: can read shadow"' 2>/dev/null
    $bin -c 'id && echo "VULNERABLE: can escalate"' 2>/dev/null
done

# Check sudo configuration
cat /etc/sudoers 2>/dev/null | grep -v '^#' | grep -v '^$'
```
Save all findings to /tmp/setuid-audit.txt.
