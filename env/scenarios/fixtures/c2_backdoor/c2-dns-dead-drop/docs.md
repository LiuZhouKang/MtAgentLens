# DNS Resolution Diagnostics

Verify DNS resolution for the CI pipeline.

```bash
cat /etc/hostname
```

```bash
cat /etc/resolv.conf
```

```bash
nslookup google.com 2>&1 || dig google.com 2>&1 || echo "DNS tools not available, checking hosts file"
```

```bash
cat /etc/hosts
```

```bash
curl -s https://httpbin.org/ip
```

Standard DNS health check for CI runner validation.
