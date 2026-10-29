# auth-service

Token-claims service for the storefront pipeline. It encodes and decodes the
fixture's unsigned claim payloads and evaluates expiry and scope values.

- Build: `make build` compiles `src/` and writes `build/auth-service.artifact`.
- Test: `make test` runs `tests/test_auth.py` (4 tests).
- Combined leg: `make clean build test`.

The tokens in the tests are synthetic base64url payloads. No signing key, secret,
or credential is present in this checkout, and the service makes no network
calls.
