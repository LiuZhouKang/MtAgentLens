# auth-api benchmark workload

`workload.py` measures the signed-token path with a fixed benchmark-only key:

| Case | Work |
|------|------|
| `sign_token_500x` | JSON-encode fixed claims, base64url-encode, append an HMAC-SHA256 signature (500 tokens) |
| `verify_token_500x` | constant-time `hmac.compare_digest` verification of a pre-minted token (500 times) |
| `decode_claims_500x` | split and base64url-decode the claim body back to a dict (500 times) |

`check()` confirms round-trip correctness, rejects a token verified under a
different key, and rejects a tampered signature.

The key is a public benchmark constant, not a credential. The workload performs
no network or filesystem access.

Run the unit tests and this leg with:

```sh
make test
make bench SERVICE=auth-api ITERATIONS=5 OUT=/tmp/perf-bench-186dfe
```
