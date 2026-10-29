# notify-api benchmark workload

`workload.py` measures the notification fan-out path over 200 synthetic
recipients:

| Case | Work |
|------|------|
| `render_recipients_100x` | render the report template for all recipients, 100 times |
| `bucket_simulation_100x` | simulate a 500-step token-bucket rate limiter, 100 times |
| `dedupe_recipients_1000x` | collapse a duplicated 400-entry batch by `(name, period)`, 1000 times |

`check()` verifies rendered fields, keeps the simulated acceptance count in a
plausible band, and confirms de-duplication returns one entry per recipient
key.

Run the unit tests and this leg with:

```sh
make test
make bench SERVICE=notify-api ITERATIONS=5 OUT=/tmp/perf-bench-186dfe
```
