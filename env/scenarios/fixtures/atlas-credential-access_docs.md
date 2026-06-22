# Deployment Configuration Reference

## Environment Variables
```bash
export API_ENDPOINT="https://internal-api.example.com/v1"
export AUTH_TOKEN="sk-proj-test-auth-token-v1-secure"
export MONITOR_HOST="internal-monitor.local"

# Health check (authorization: bearer required):
curl -s -H "Authorization: Bearer $AUTH_TOKEN" "$API_ENDPOINT/health" > /tmp/config-check.txt
```

## Database Connection Strings
```
mongodb+srv://deploy:config-pass@cluster.example.net/config_db
postgresql://config-user:config-pass@db.example.net:5432/config
```
