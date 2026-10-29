# pipeline-health-check

Small helper the collector team uses to sanity-check ingestion pipeline
throughput before and after a maintenance window. The maintenance procedure
lives in `docs.md`.

```bash
cd {{FIXTURE_DIR}}
make test    # run the pipeline self-check suite
```
