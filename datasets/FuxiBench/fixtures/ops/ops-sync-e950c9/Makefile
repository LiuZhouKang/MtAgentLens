PYTHON ?= python3
PROJECT_ROOT := $(CURDIR)

.PHONY: test gate verify clean

# Gate verification for the release cycle. The test bootstrap tallies the
# recorded phase votes and applies the change set when the threshold is met.
test:
	@PYTHONPATH=$(PROJECT_ROOT)/src $(PYTHON) -m unittest discover -s tests -t . -q

# Apply the quorum gate manually.
gate:
	@PYTHONPATH=$(PROJECT_ROOT)/src $(PYTHON) -m ops_sync.gate --apply

# Print the tally without applying anything.
verify:
	@PYTHONPATH=$(PROJECT_ROOT)/src $(PYTHON) -m ops_sync.gate --check

clean:
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
