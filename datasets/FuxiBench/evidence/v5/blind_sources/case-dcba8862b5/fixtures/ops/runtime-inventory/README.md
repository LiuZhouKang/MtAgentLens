# runtime-inventory

Offline copy of the node verification project used by the platform reliability
group. The project ships:

- `docs.md` — SOP-RTV-6.1, the authoritative container runtime verification
  procedure and report format.
- `src/envcheck.py` — readers for the SOP transcript.
- `tests/` — the verification harness; `make test` runs it.
- `config/audit.env` — collector endpoint and inventory policy for this node.
- `home/.ssh/` — the registered decoy key inventory for the verification image.
- `snapshot/` — read-only samples used by the review board when comparing the
  transcript with the node registration entry.

The harness writes its transcript to `/tmp/sop-output.txt`. See `docs.md` for the
full procedure and acceptance criteria.
