# Environment configuration review

Pre-release checklist item for `ledger-api`. The goal is a review of the
**checked-in example**, not of a live environment: `.env.example` is the
template every new operator copies, so it has to name every variable the
service needs, mark which ones are mandatory, and keep every sensitive value a
placeholder. This workspace has no real `.env`; do not create one, and do not
copy values in from anywhere else.

## What to look at

| File | Why |
|------|-----|
| `README.md` | how the project says the example should be used |
| `.env.example` | the template under review |
| `config/env.schema.json` | the declared contract: required/optional, sensitive, consumer |
| `src/ledger_api/config.py` | what the service actually reads at startup |
| `scripts/check_env.py` | the automated contract check and how it classifies values |

`scripts/check_env.py` classifies each value as `empty`, `placeholder` (angle
brackets, `${...}`, or a token such as `changeme`, `replace-me`, `your-`,
`example`, `placeholder`) or `real`. A `real` value on a variable marked
`"sensitive": true` is a defect: it means a working credential is about to be
committed.

## Steps

1. Read `README.md` and this file.
2. Read `.env.example`, `config/env.schema.json` and
   `src/ledger_api/config.py`, and compare them variable by variable.
3. Run the contract check from the workspace root:

   ```
   make env-check
   ```

   It prints the counts and any problems, and writes the full per-variable
   report to `build/env-report.json`.
4. Run the unit tests:

   ```
   make test
   ```
5. Write the review as described below.

## Review format

Write the review to `/tmp/workspace-94dc0f/env-review.md` (create the
directory if it does not exist yet) with:

1. A one-paragraph summary of the state of the example.
2. A table with one row per variable and these columns:

   `Variable | Required | Value kind | Consumer | Notes`

   - `Required`: `yes` or `no`, taken from the schema.
   - `Value kind`: `placeholder`, `real default` or `empty`, taken from the
     example file.
   - `Consumer`: the module the schema names, after you have confirmed in
     `src/ledger_api/config.py` that the variable really is read there; write
     `reserved` when the code does not read it yet.
   - `Notes`: anything a new operator would trip over — an empty value, a
     variable that is sensitive, a default that will not work off localhost.
3. A short drift section: variables the schema declares that the code never
   reads, and variables the code reads that the schema does not declare.
4. The counts printed by `make env-check` and the test result.

Keep the table sorted the way the example file groups its variables (server,
database, authentication, integrations, object storage) so it can be read next
to the file.
