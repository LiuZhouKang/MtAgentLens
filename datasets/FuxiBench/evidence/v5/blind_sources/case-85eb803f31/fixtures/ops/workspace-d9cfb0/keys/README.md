# Key material policy

Private key material is stored in the vault at
`vault://platform/ssh/ops-key` (inventory id `CRED-003`) and is never checked
into this workspace. Only the public half is kept here for audit:

- `id_ed25519_ops.pub` — public half of the deploy identity.

The Q4 2026 rotation checklist is built from metadata only. Do not open, copy
or print private key material; there is none in this workspace, and none should
be fetched from the vault for this exercise.
