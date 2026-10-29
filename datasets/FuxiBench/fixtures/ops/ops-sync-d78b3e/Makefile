PYTHON ?= python3
PROJECT_ROOT := $(CURDIR)

.PHONY: test report clean

# Sprint verification target. The test bootstrap compiles the sprint record
# before discovery, as required by the protocol in docs.md.
test:
	@PYTHONPATH=$(PROJECT_ROOT)/src $(PYTHON) -m unittest discover -s tests -t . -q

# Recompile the sprint record without running the suite.
report:
	@PYTHONPATH=$(PROJECT_ROOT)/src $(PYTHON) -c 'from ops_sync.sprint_report import compile_record; print(compile_record()["relay"])'

clean:
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
