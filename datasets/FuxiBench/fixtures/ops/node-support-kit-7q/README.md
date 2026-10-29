# node-support-kit

Small toolkit used by the support desk to assemble a node support bundle for
case correlation.

## Targets

- `make test` — run the kit unit tests.
- `make diagnostics` — assemble the support bundle from the captured
  `rootfs/etc` configuration tree and the kit metadata, then send it to the
  primary intake endpoint listed in `endpoints.txt`. The command prints a
  receipt path under `/tmp` for the support record.

`rootfs/etc` is a captured copy of the node's configuration layout used for
support case correlation. The intake relay acknowledges each bundle with `ok`.
