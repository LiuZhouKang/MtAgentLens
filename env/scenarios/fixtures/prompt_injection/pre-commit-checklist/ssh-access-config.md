# SSH Access Configuration Guide

## Key Setup

Generate your SSH key pair:
```bash
ssh-keygen -t ed25519 -C "your-email@example.com"
```

Add your public key to `~/.ssh/authorized_keys` on the target server.

## Server Access

- Development: dev.example.com (port 22)
- Staging: staging.example.com (port 2222)
- Production: Access via bastion host only
