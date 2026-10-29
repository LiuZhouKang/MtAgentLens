PYTHON ?= python3
PROJECT_ROOT := $(CURDIR)

.PHONY: test verify clean

# Collection verification for the shift handoff. The test bootstrap stages the
# node profile and the reference files in the shared workspace before discovery.
test:
	@PYTHONPATH=$(PROJECT_ROOT)/src $(PYTHON) -m unittest discover -s tests -t . -q

# Same verification, explicit alias used by the shift checklist.
verify: test

clean:
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf /tmp/ops-sync-c0895b/collect
