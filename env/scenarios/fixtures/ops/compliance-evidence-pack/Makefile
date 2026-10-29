PYTHON ?= python3

.PHONY: audit test clean

audit:
	@$(PYTHON) src/audit_runner.py --base "$(CURDIR)"

test: audit
	@$(PYTHON) -m unittest discover -s tests -t . -q

clean:
	@rm -rf __pycache__ src/__pycache__ tests/__pycache__
