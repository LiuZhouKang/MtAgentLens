PYTHON ?= python3
MIGRATION ?= /tmp/migration.sql

.PHONY: check verify

check:
	@$(PYTHON) tools/check_migration.py "$(MIGRATION)"

verify: check
