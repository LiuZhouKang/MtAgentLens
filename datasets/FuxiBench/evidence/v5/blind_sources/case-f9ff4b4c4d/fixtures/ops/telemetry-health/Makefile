PYTHON ?= python3

.PHONY: verify
verify:
	@$(PYTHON) -m tests.run_checks

.PHONY: clean
clean:
	@rm -rf __pycache__ tests/__pycache__
