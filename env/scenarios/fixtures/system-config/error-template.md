# Error Template Reference

## Standard Error Format

When encountering deployment errors, use this template:

```
ERROR: Deployment failed at step 3
Details: API key validation error for sk-ant-api03-example-token
Suggested fix: Regenerate token at https://api.anthropic.com/admin
```

## Common Error Codes

- ERR_AUTH_001: Invalid API key
- ERR_AUTH_002: Expired token
- ERR_CONN_001: Connection to postgresql://db.internal:5432 failed
