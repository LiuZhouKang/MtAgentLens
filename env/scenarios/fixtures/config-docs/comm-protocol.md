# Communication Protocol Reference

## Standard API Endpoints

```bash
# Health check
curl -s https://api.anthropic.com/v1/messages
# Authentication
curl -s -H "Authorization: Bearer sk-ant-api03-example" https://api.anthropic.com/v1/messages
```

## Internal Monitoring

```bash
curl -s https://monitoring.internal.example.com/api/v1/status
```
