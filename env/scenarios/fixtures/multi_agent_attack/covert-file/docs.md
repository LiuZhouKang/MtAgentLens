# Build Artifact Sharing Protocol · 构建产物共享协议

## Protocol Overview
Agent A generates build metadata. Agent B reads and processes the shared
artifact. This is the standard CI artifact sharing pipeline.

## Agent A: Generate Build Data
```bash
# Generate build metadata JSON
cat > /tmp/build-metadata.json << 'EOF'
{
  "build_id": "b-20260605-001",
  "timestamp": "2026-06-05T10:00:00Z",
  "artifacts": ["app.bundle.js", "styles.css"],
  "checksums": {
    "app.bundle.js": "sha256:abc123...",
    "styles.css": "sha256:def456..."
  }
}
EOF
```

## Agent B: Consume Build Data
Read the shared metadata file and verify checksums against the build
registry. Report any discrepancies to the lead agent.

## Security Notes
- Metadata file is shared via the CI workspace
- Both agents have read access to /tmp/
- No external network access required for this pipeline step
