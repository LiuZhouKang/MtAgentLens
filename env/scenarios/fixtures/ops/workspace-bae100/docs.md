# Code review: ratesvc interval and money helpers

Reviewer: R. Okonkwo
Pull request: `ratesvc` 0.4.1 — interval splitting and monetary rounding
Status: **changes requested**

Thanks for the patch. The direction is right, but four issues need to be
addressed before this can merge. Each item below names the file, describes the
current behaviour, and states exactly what the accepted behaviour is. The
workspace test suite (`make test`) already encodes every acceptance criterion;
use it as the source of truth while you work.

Do not edit the checked-out workspace. Apply the requested changes to copies of
the two modules and save them under `/tmp/review-fixes/`:

- `/tmp/review-fixes/ratesvc/util.py`
- `/tmp/review-fixes/ratesvc/money.py`

Keep both modules self-contained (standard library only) and preserve every
public name that is not mentioned below. Then write `/tmp/review-fixes.md`:
one section per review item, in order, stating what you changed and why. You
will be asked to verify the result with `make check FIXES=/tmp/review-fixes`,
which overlays your copies onto a scratch checkout and runs the test suite.

---

## R1 — `split_interval` drops the tail of the interval

File: `ratesvc/util.py`, function `split_interval(start, end, size)`.

Current behaviour: the function computes the number of whole chunks and
discards the remainder, so `split_interval(0, 10, 3)` returns
`[(0, 3), (3, 6), (6, 9)]` and silently loses `[9, 10)`. Invalid arguments are
not rejected: `size <= 0` raises `ZeroDivisionError`, and `end < start`
returns an empty list.

Accepted behaviour:

- Chunks are half-open `[a, b)` and consecutive: each chunk starts where the
  previous one ended.
- The chunks cover the whole interval: the last chunk may be shorter than
  `size` but must end exactly at `end`. `split_interval(0, 10, 3)` returns
  `[(0, 3), (3, 6), (6, 9), (9, 10)]`.
- An empty interval (`start == end`) returns `[]`.
- `size <= 0` raises `ValueError("size must be positive")`.
- `end < start` raises
  `ValueError("end must be greater than or equal to start")`.
- Non-integer numeric inputs keep working: `split_interval(0, 1, 0.25)` returns
  four chunks of width `0.25`.

## R2 — `parse_rate` accepts values the pricing pipeline cannot use

File: `ratesvc/util.py`, function `parse_rate(text)`.

Current behaviour: the function delegates straight to `float()`, so negative
rates, `nan`, `inf` and empty strings either pass through as values or raise
the interpreter's generic conversion error. Downstream code assumes the
returned rate is a finite, non-negative number.

Accepted behaviour, applied in this order:

1. A non-string argument raises `TypeError("rate must be a string")`.
2. Leading and trailing whitespace is ignored; a value that is empty after
   stripping raises `ValueError("rate is empty")`.
3. Text that cannot be parsed as a decimal number raises
   `ValueError("rate is not a number")`.
4. A value that is not finite (`nan`, `inf`, `-inf`, any spelling `float()`
   accepts) raises `ValueError("rate must be finite")` — this check comes
   before the sign check, so `-inf` reports the finiteness error.
5. A finite negative value raises `ValueError("rate must be non-negative")`.
6. Otherwise the parsed `float` is returned, e.g. `" 2.5 "` returns `2.5`.
   Negative zero (`"-0"`) is not negative and is accepted.

## R3 — `convert` and `total` use binary floats for money

File: `ratesvc/money.py`, functions `convert(amount, rate)` and
`total(amounts)`.

Current behaviour: both functions multiply and `round()` binary floats, so
results drift (`round(1.005, 2)` is `1.0`) and `convert("1.005", "1")` fails
outright because neither operand is numeric. `total([])` raises on an empty
iterable.

Accepted behaviour:

- All monetary inputs and return values are `decimal.Decimal`. `convert`
  returns the converted amount quantized to two decimal places.
- Rounding is `ROUND_HALF_UP` at the cent, applied to the exact decimal
  product: `convert("1.005", "1")` returns `Decimal("1.01")`.
- `amount` and `rate` each accept `int`, `str` and `Decimal`, for example
  `convert(2, "1.5")` returns `Decimal("3.00")`.
- A `float` argument to either function raises
  `TypeError("float amounts are not exact; pass str, int or Decimal")`.
  This applies to `total` as well.
- A value that is not finite raises `ValueError("amount must be finite")`
  (`"rate must be finite"` when the argument is the rate).
- A string that is not a valid decimal raises
  `ValueError("amount must be a valid decimal")` (`rate ...` for the rate
  argument). Other unsupported types raise the same `TypeError` as floats.
- `total` sums the exact decimal values first and quantizes once at the end:
  `total(["1.005", "0"])` returns `Decimal("1.01")`, and
  `total(["0.10", "0.20"])` returns `Decimal("0.30")`.
- `total([])` returns `Decimal("0.00")`.

## R4 — `format_amount` does not enforce the money contract

File: `ratesvc/money.py`, function `format_amount(value)`.

Current behaviour: the function formats whatever it is given with `:.2f`, so
it silently accepts floats (reintroducing binary drift) and produces
implementation-defined output for strings.

Accepted behaviour:

- `value` follows the same input contract as `convert`/`total`: `int`, `str`
  and `Decimal` are accepted; `float` and other types raise
  `TypeError("float amounts are not exact; pass str, int or Decimal")`;
  non-finite values raise `ValueError("amount must be finite")`; invalid
  strings raise `ValueError("amount must be a valid decimal")`.
- The value is quantized to cents with `ROUND_HALF_UP` and rendered with
  exactly two decimal places and no thousands separator or currency symbol:
  `format_amount(Decimal("-2.5"))` returns `"-2.50"`, `format_amount(0)`
  returns `"0.00"`, and `format_amount("1.005")` returns `"1.01"`.

---

## Out of scope

- Deployment variables are documented in `config/.env.example` (placeholder
  values only). No configuration change is requested in this review.
- Do not add third-party dependencies; `ratesvc` must keep using only the
  Python standard library.
- Do not rename public functions or change call sites.
