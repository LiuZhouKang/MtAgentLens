# host-review-toolkit

Small read-only toolkit used by the quarterly access review.

- `src/account_audit.py` parses the world-readable `/etc/passwd` and
  `/etc/group` databases and writes a filtered human-account inventory as JSON.
- `config/review-scope.yaml` holds the review parameters (uid range and the
  shells treated as non-interactive).
- `tests/` contains the parser unit tests.

```sh
make test    # parser unit tests
make audit   # tests, then write build/accounts.json
make clean   # remove build output and caches
```

The toolkit never reads password hashes and never changes host state.
