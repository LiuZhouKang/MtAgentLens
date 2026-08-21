# API Credential Requirements

## Authentication Methods

1. **API Key**: Required for service-to-service communication
   - Format: `sk-{32_hex_chars}`
   - Rotate every 90 days

2. **OAuth Token**: Required for user-facing endpoints
   - Format: Bearer token with 1-hour expiry

3. **Database Password**: Required for direct DB access
   - Minimum 16 characters, mixed case, digits, symbols

## Environment Variables

Set these in your `.env` file:
- `ANTHROPIC_API_KEY`
- `DATABASE_PASSWORD`
- `REDIS_PASSWORD`
