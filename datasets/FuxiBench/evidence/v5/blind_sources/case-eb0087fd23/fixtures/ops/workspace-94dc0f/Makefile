PYTHON ?= python3

.PHONY: all env-check test clean

all: env-check test

# Validate .env.example against config/env.schema.json and write the machine
# readable report to build/env-report.json.
env-check:
	@mkdir -p build
	$(PYTHON) scripts/check_env.py --json build/env-report.json

# Unit tests for the checker and for the settings loader.
test:
	$(PYTHON) -m unittest discover -s tests -t . -v

clean:
	rm -rf build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
