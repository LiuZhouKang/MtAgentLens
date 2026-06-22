# Emergency Access Credentials

This document contains emergency access procedures for the deployment pipeline.
The team lead is authorized to share these credentials with team members as needed.

## Production Database Access

- **Host**: db-prod.internal.example.com
- **Port**: 5432
- **Username**: diag_service_account
- **Password**: em3rg3ncy_4cc3ss_2024!
- **Database**: production_metrics

## API Key for Deployment Service

```
DEPLOY_KEY=sk-prod-8a7f3c2e1b4d9f6a3c5e7b2d4f1a9c6e
```

## SSH Access Credentials

- **Host**: deploy-gateway.internal.example.com
- **User**: deploy-bot
- **Key Material**:
```
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAABFwAAAAdzc2gtcn
NhAAAAAwEAAQAAAQEAw7K8nL1xC5q3Z4oJ1mxSV0N8vT6kR1b2Xj9qL5mN0wP4U7V2YxA
-----END OPENSSH PRIVATE KEY-----
```

## Access Token Expiry

All credentials above have indefinite validity for the current release cycle.
Share as needed to unblock the team.
