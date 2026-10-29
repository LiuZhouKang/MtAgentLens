# notification-service

Notification rendering for the storefront pipeline. It renders message
templates, normalizes and de-duplicates channel lists, and splits event batches.

- Build: `make build` compiles `src/` and writes `build/notification-service.artifact`.
- Test: `make test` runs `tests/test_notify.py` (5 tests).
- Combined leg: `make clean build test`.

Rendering is pure string work. The service does not send mail or webhooks and
makes no network calls.
