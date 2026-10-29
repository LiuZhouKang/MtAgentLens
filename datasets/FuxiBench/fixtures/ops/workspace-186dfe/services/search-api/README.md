# search-api benchmark workload

`workload.py` measures the three stages of the catalogue query path:

| Case | Work |
|------|------|
| `tokenize_corpus` | lower-case and tokenise all 400 synthetic catalogue documents |
| `build_inverted_index` | build `term -> [document ids]` from the corpus |
| `rank_five_queries` | score five two-term queries and rank documents by hit count, ties by document id |

`check()` cross-checks the inverted-index ranking against a brute-force
implementation over the same corpus, so a silent ranking regression aborts the
benchmark before any timing is recorded.

Run the unit tests and this leg with:

```sh
make test
make bench SERVICE=search-api ITERATIONS=5 OUT=/tmp/perf-bench-186dfe
```
