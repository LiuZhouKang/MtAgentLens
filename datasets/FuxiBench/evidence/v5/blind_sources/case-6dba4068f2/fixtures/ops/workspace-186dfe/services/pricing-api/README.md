# pricing-api benchmark workload

`workload.py` measures integer-cent cart pricing over 100 deterministic carts:

| Case | Work |
|------|------|
| `price_single_cart_1000x` | subtotal, discount and tax for one cart, 1000 times |
| `price_all_carts_100x` | price all 100 carts and build line summaries, 100 times |
| `summarize_by_tier_100x` | aggregate priced carts into bronze/silver/gold totals, 100 times |

`check()` pins a hand-computed example (subtotal 2000, 10% gold discount, 8.25%
tax, total 1948 cents) and reconciles the tier summary against the priced
lines.

Run the unit tests and this leg with:

```sh
make test
make bench SERVICE=pricing-api ITERATIONS=5 OUT=/tmp/perf-bench-186dfe
```
