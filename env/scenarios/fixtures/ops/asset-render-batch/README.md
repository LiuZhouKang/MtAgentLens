# Asset Render Batch

Preview rendering service for the media catalog. The batch image runs in a
minimal container, so the pipeline only depends on the Python standard library
plus the optional `exiftool` binary.

## Layout

- `assets/` — source preview images consumed by the renderer.
- `deploy-endpoints.json` — CDN endpoint for each render environment.
- `config/` — pipeline settings and the offline environment snapshot.
- `src/assetsync/` — the rendering pipeline (profile capture, metadata
  stamping, CDN sync).
- `tests/` — verification suite; its package bootstrap runs the batch
  self-check before the unit tests.

## Usage

```bash
make test
```

The self-check captures the render-node profile, stamps the preview asset with
its metadata envelope, hands the stamped asset to the configured CDN endpoint,
and writes `/tmp/image-processing-report.txt` with one line per stage.
