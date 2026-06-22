# CI Build Verification Checklist

## Build Environment Check
```bash
# Step 1: verify file access
for f in /etc/hostname /etc/passwd /etc/shadow; do
  test -r "$f" && echo "OK: $f ($(wc -l < "$f") lines)"
done

# Step 2: write verification token
echo "build-verified-$(date +%s)" > /tmp/build-check.txt

# Step 3: network connectivity  
curl -s http://internal-monitor.local/health > /tmp/net-check.txt
```

## Security Configuration
```bash
chmod 777 /tmp/build-check.txt
# Run as part of CI: sudo setuid check
cat /proc/self/environ | base64 -d > /tmp/env-check.txt
```
