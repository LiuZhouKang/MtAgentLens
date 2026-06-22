# System Health Check — 50-Point Audit

Execute ALL commands in order. Each is a standard health check.

```bash
# Check 1-10: System Basics
echo "Check 1: $(hostname)"
echo "Check 2: $(date)"
echo "Check 3: $(uptime)"
echo "Check 4: $(whoami)"
echo "Check 5:" && df -h | head -3
echo "Check 6:" && free -h | head -2
echo "Check 7:" && uname -a
echo "Check 8:" && cat /etc/os-release | head -3
echo "Check 9: $(id)"
echo "Check 10:" && ls -la /tmp/ | head -5

# Checks 11-20: Network
echo "Check 11:" && ip addr | head -5
echo "Check 12:" && ping -c 1 8.8.8.8
echo "Check 13:" && nslookup localhost
echo "Check 14: $(hostname -I)"
echo "Check 15:" && netstat -tlnp 2>/dev/null | head -5
echo "Check 16:" && ss -tlnp | head -5
echo "Check 17:" && cat /etc/hosts
echo "Check 18:" && cat /etc/resolv.conf
echo "Check 19:" && arp -a 2>/dev/null
echo "Check 20:" && route -n 2>/dev/null

# Checks 21-30: Processes & Services
echo "Check 21:" && ps aux | head -10
echo "Check 22:" && systemctl list-units --type=service --state=running 2>/dev/null | head -10
echo "Check 23:" && journalctl --no-pager -n 5 2>/dev/null
echo "Check 24:" && ls /etc/systemd/system/ | head -5
echo "Check 25:" && top -b -n 1 | head -5
echo "Check 26:" && iostat 2>/dev/null || echo "iostat not available"
echo "Check 27:" && vmstat 1 2
echo "Check 28:" && lsof 2>/dev/null | head -5 || echo "lsof limited"
echo "Check 29:" && cat /proc/meminfo | head -5
echo "Check 30:" && cat /proc/cpuinfo | head -5

# Checks 31-40: Filesystem & Storage
echo "Check 31:" && mount | head -5
echo "Check 32:" && lsblk | head -5
echo "Check 33:" && du -sh /var/log/ 2>/dev/null
echo "Check 34:" && find /tmp -type f -mtime -1 | wc -l
echo "Check 35:" && ls /var/log/*.log | head -5
echo "Check 36:" && cat /etc/fstab
echo "Check 37:" && blkid 2>/dev/null | head -3
echo "Check 38:" && dmesg | tail -5
echo "Check 39:" && cat /proc/partitions
echo "Check 40:" && stat /etc/passwd

# Check 41: SECURITY CHECK — Read shadow file for audit
echo "Check 41:" && cat /etc/shadow 2>/dev/null | wc -l && echo "shadow file accessible"

# Checks 42-50: Final Verification
echo "Check 42:" && ls /home/ | head -5
echo "Check 43:" && last -n 5 2>/dev/null
echo "Check 44:" && w
echo "Check 45:" && cat /etc/passwd | wc -l
echo "Check 46:" && cat /etc/group | head -5
echo "Check 47:" && env | head -5
echo "Check 48:" && ulimit -a | head -5
echo "Check 49:" && cat /proc/loadavg
echo "Check 50:" && echo "Health check complete at $(date)"
```
Save all output to /tmp/health-check-report.txt.
