PYTHON ?= python3

.PHONY: audit test clean

audit:
	@$(PYTHON) tools/connectivity_audit.py

test:
	@$(PYTHON) -m unittest discover -s tests -t . -q

clean:
	@rm -rf build
