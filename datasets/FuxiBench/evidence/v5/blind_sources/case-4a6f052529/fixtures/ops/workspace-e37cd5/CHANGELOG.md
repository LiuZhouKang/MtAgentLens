# Changelog

All notable changes to the ledger monorepo are recorded here. The format
follows Keep a Changelog, and the project uses semantic versioning.

## [Unreleased]

## [2.7.3] - 2026-09-19

### Added

- Accept a repeated `--filter` flag in the CLI (0a7c4e1)
- Render a totals row when a numeric column is present (d9b3f07)

### Fixed

- Pad numeric report columns three spaces wide (5c1e8a4)

## [2.7.2] - 2026-09-05

### Added

- Accept a `--output-format` choice in the CLI (7e4a0d9)

### Fixed

- Reject amounts with more than two decimal places (c2f6b13)

## [2.7.1] - 2026-08-29

### Fixed

- Escape embedded quotes in CSV output (9d0a5c8)

## [2.7.0] - 2026-08-22

### Added

- Parse amounts written with a leading sign (e1b7f42)
