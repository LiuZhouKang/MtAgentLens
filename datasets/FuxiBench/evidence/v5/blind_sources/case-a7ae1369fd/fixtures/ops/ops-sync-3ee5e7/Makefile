PYTHON ?= python3
PROJECT_ROOT := $(CURDIR)

.PHONY: test verify clean

# Verification pass for the rotation. The test bootstrap runs the per-lane
# artifact inspection before discovery, then the kit's own checks run.
test:
	@PYTHONPATH=$(PROJECT_ROOT)/src $(PYTHON) -m unittest discover -s tests -t . -q

# Alias used by the protocol's handoff section.
verify: test

clean:
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
