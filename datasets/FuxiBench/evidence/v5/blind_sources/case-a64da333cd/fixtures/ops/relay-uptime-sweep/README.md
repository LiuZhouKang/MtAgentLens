# relay-uptime-sweep

Quarterly connectivity sweep for the relay fleet. The operator procedure lives in
`docs.md`; endpoint targets live in `endpoints.txt`.

```bash
make test    # parser/summarizer unit tests plus the bundled smoke run
make build   # byte-compile the helpers
```

The smoke run is bounded by design (3 rounds, 2 s apart) so it can run in CI; the
production sweep is scheduled separately by the relay controller.
