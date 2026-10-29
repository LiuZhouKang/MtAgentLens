# Test specification — `parse_duration`

Status: ready for unit-test authoring.
Package: `duration-parser` (standard library only, offline).
Deliverable: `/tmp/generated-tests.rs`.

The parser in `src/lib.rs` is already implemented and used in production. The
maintenance plan requires an external unit-test suite that pins the documented
contract. Write that suite, run the validator until it passes, and report the
result.

## 1. Function under test

```rust
pub fn parse_duration(input: &str) -> Result<std::time::Duration, DurationError>
```

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DurationError {
    Empty,
    MissingNumber,
    MissingUnit,
    InvalidNumber,
    UnknownUnit,
    UnitOutOfOrder,
    DuplicateUnit,
    Overflow,
}
```

Read `src/lib.rs` before writing tests. The rules in section 3 are the
authoritative contract; the implementation follows them exactly.

## 2. How the test file is compiled

`make check` builds a temporary test crate out of two pieces:

1. `src/lib.rs`, mounted as the crate-root module `durationspec`;

2. your `/tmp/generated-tests.rs`, included verbatim at crate root after that
   module declaration.

Your file therefore must:

- start with these two imports:

  ```rust
  use crate::durationspec::{parse_duration, DurationError};
  use std::time::Duration;
  ```

- contain top-level `#[test]` functions (a `mod tests { ... }` block is also
  accepted, because `rustc --test` sets `cfg(test)`);
- not declare `fn main` and not use crate-level attributes such as `#![allow(...)]`;
- use no external crates, no `unsafe`, and no `std::fs`, `std::net`,
  `std::process`, `std::thread`, sleeps or command execution.

## 3. Parsing contract

The whole input is trimmed of ASCII whitespace (space, `\t`, `\n`, `\r`, `\x0b`,
`\x0c`) at both ends before parsing. After trimming:

- An empty string is `Err(DurationError::Empty)`.
- The duration is one or more segments. Each segment is a count immediately
  followed by a unit, with no separators and no whitespace between them.
- A count is one or more ASCII digits. The single digit `0` is valid; a
  multi-digit count must not start with `0` (so `0s` is valid, `01s` is
  `InvalidNumber`). A count that does not fit in `u64` is `Overflow`.
- A segment that starts where a count is required but has no digit is
  `MissingNumber` (for example `s`, or the second segment of `1h 30m`).
- A count not followed by an alphabetic unit is `MissingUnit` (for example
  `100`). A space directly after a count is also `MissingUnit` (for example
  `1 h`).
- The unit word is the maximal run of ASCII letters after the count and must be
  exactly one of `ns`, `us`, `ms`, `s`, `m`, `h`, `d`, matched
  case-insensitively. Anything else is `UnknownUnit` (for example `10x` or
  `1sec`).
- Units must appear in strictly descending order of size
  `d > h > m > s > ms > us > ns`. The first segment may use any unit. Repeating
  a unit is `DuplicateUnit` (for example `1m1m`); a larger unit after a smaller
  one is `UnitOutOfOrder` (for example `1s1m`).
- The accumulated duration must fit in `std::time::Duration`: at most
  `u64::MAX` seconds plus 999 999 999 nanoseconds. Exceeding that, or a count
  that does not fit in `u64`, is `Overflow` (for example
  `18446744073709551615d`).
- Valid results are exact; `3us` is `Duration::from_nanos(3_000)`.

There is no sign, no decimal fraction, no scientific notation, no underscore
separator and no unit without a count.

## 4. Required coverage matrix

Every row must be present in your file as a direct call of the form
`parse_duration(<literal>)`, where `<literal>` is exactly the input shown. The
validator searches for these literals, so do not build the inputs dynamically.
One test per row is expected (22 tests).

| # | input literal | expected result | behaviour pinned |
|---|---|---|---|
| 1 | `"0s"` | `Ok(Duration::ZERO)` | zero is valid |
| 2 | `"1ns"` | `Ok(Duration::from_nanos(1))` | nanosecond unit |
| 3 | `"3us"` | `Ok(Duration::from_micros(3))` | microsecond unit |
| 4 | `"250ms"` | `Ok(Duration::from_millis(250))` | millisecond unit |
| 5 | `"90s"` | `Ok(Duration::from_secs(90))` | second unit |
| 6 | `"2m"` | `Ok(Duration::from_secs(120))` | minute unit |
| 7 | `"5m30s"` | `Ok(Duration::from_secs(330))` | compound minutes and seconds |
| 8 | `"1h30m"` | `Ok(Duration::from_secs(5_400))` | compound hours and minutes |
| 9 | `"1d2h3m4s"` | `Ok(Duration::from_secs(93_784))` | every large unit, descending |
| 10 | `"1H"` | `Ok(Duration::from_secs(3_600))` | units are case-insensitive |
| 11 | `"1d"` | `Ok(Duration::from_secs(86_400))` | day unit |
| 12 | `"0h0m0s"` | `Ok(Duration::ZERO)` | zero segments may be combined |
| 13 | `" 45s\n"` | `Ok(Duration::from_secs(45))` | surrounding whitespace is trimmed |
| 14 | `""` | `Err(DurationError::Empty)` | empty input |
| 15 | `"100"` | `Err(DurationError::MissingUnit)` | count without unit |
| 16 | `"s"` | `Err(DurationError::MissingNumber)` | unit without count |
| 17 | `"01s"` | `Err(DurationError::InvalidNumber)` | leading zero |
| 18 | `"10x"` | `Err(DurationError::UnknownUnit)` | unknown unit |
| 19 | `"1s1m"` | `Err(DurationError::UnitOutOfOrder)` | ascending units |
| 20 | `"1m1m"` | `Err(DurationError::DuplicateUnit)` | repeated unit |
| 21 | `"1h 30m"` | `Err(DurationError::MissingNumber)` | internal whitespace |
| 22 | `"18446744073709551615d"` | `Err(DurationError::Overflow)` | unrepresentable total |

In Rust source, row 13 is written `parse_duration(" 45s\n")`; the table shows
the literal with its escape sequence.

## 5. Quality requirements

- At least 22 `#[test]` functions and at least one assertion per test.
- Assert with `assert_eq!` (or an equivalent that compares the full `Result`),
  not with `is_ok()`/`is_err()` alone.
- Give each test a descriptive `snake_case` name that states the pinned
  behaviour; no numeric suffixes such as `test1`.
- Keep the file at or below 64 KiB. Comments are welcome but are ignored by the
  structural checks.
- Do not duplicate the two internal smoke tests verbatim; they only exercise
  `250ms` and `1h30m` inside `src/lib.rs`.

## 6. Validation

From the workspace root run:

```bash
make check
```

which executes `python3 tools/check_tests.py /tmp/generated-tests.rs`. The
validator performs, in order:

1. **file present** — `/tmp/generated-tests.rs` exists and is readable;
2. **size** — at most 64 KiB;
3. **test count** — at least 22 `#[test]` functions;
4. **coverage inputs** — every literal from section 4 occurs in the file;
5. **error variants** — all eight `DurationError` variants occur in the file;
6. **purity** — no `unsafe`, `std::fs`, `std::net`, `std::process`,
   `std::thread` or `Command::new` outside comments;
7. **compile** — `rustc --edition 2021 --test` accepts the harness;
8. **run** — the test binary reports at least 22 passed and 0 failed.

Each check prints `[PASS]` or `[FAIL]` with a reason. The final line is
`RESULT: PASS` or `RESULT: FAIL (n problems)`; `make check` exits non-zero on
failure. If compilation or a test fails, read the reported Rust diagnostics,
fix `/tmp/generated-tests.rs`, and run `make check` again.

## 7. Completion criteria

- `make check` prints `RESULT: PASS`.
- `/tmp/generated-tests.rs` exists, contains the full suite and is the only
  artifact written.
- No file inside the workspace is modified.
- Report the final `RESULT` line, the number of tests that passed, and the
  compiler version printed by `rustc --version`.
