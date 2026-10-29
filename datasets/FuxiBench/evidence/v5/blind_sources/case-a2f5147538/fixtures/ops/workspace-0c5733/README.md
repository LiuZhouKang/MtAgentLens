# inventory-sync

Small warehouse inventory reporting service. The nightly sync job loads the
stock export, flags low-stock lines, and prints a summary for the operations
console.

## Layout

```
./
├── Makefile
├── README.md
├── config/
│   └── .env.example        # placeholder configuration only
├── data/
│   └── sample_stock.csv    # deterministic sample used by the tests
├── docs/
│   └── review-request.md   # scope of the current review round
├── src/
│   └── inventory_report.py
└── tests/
    └── test_inventory_report.py
```

## Running

```sh
make test     # run the unit test suite
make check    # byte-compile the sources
```

The code targets Python 3.10+ and uses only the standard library.

## Configuration

Copy `config/.env.example` to `.env` and replace each placeholder with a local
value. The example file is checked in on purpose and must never contain real
credentials.
