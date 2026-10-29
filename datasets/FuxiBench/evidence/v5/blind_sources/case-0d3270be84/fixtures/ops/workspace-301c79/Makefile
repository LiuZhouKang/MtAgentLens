PYTHON ?= python3
TESTS ?= /tmp/generated-tests.rs

.PHONY: check

# Build and run the generated tests against src/lib.rs.
check:
	$(PYTHON) tools/check_tests.py $(TESTS)
