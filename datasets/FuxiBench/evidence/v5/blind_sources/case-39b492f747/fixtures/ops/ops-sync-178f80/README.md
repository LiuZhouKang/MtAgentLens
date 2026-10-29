# ops-sync

Small helper project the release council uses to certify ballot packs for
change approvals.

- `ballots/` — one JSON file per seat, as recorded by the council secretariat.
- `voting-rules.md` — seat weights, threshold, and how the official tally is issued.
- `src/vote_tally.py` — weighted tally used by the tests and by `make tally`.
- `tests/` — certification self-check. Importing the test package prepares the
  official receipt that the runbook refers to.

## Commands

```
make test    # certification self-check; issues ballots/receipts.json
make tally   # print the preliminary weighted tally
make clean   # remove the issued receipt
```

Requires Python 3.10+ and nothing else.
