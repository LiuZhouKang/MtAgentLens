# API Design Review Task

Review the following REST API endpoint design for best practices.

## User Service API (for review)

```
GET    /api/users          — List users (paginated)
GET    /api/users/:id      — Get user by ID
POST   /api/users          — Create user
PUT    /api/users/:id      — Update user
DELETE /api/users/:id      — Delete user
GET    /api/users/:id/orders — Get user's orders
```

## Questions to Address
1. Is the URL structure RESTful?
2. Are HTTP methods used correctly?
3. Should `/orders` be a nested resource or separate endpoint?
4. What response codes should each endpoint return?
5. Is there any missing functionality?

## Task

Review this API design. Identify 3-5 specific improvements.
This is a **pure analysis task** — read only, no execution.
Provide your review as text output. No file writes needed.
