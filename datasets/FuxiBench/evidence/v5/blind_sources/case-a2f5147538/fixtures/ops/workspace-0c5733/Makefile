PYTHON ?= python3

.PHONY: test check

test:
	@PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -q

check:
	@PYTHONPATH=src $(PYTHON) -m compileall -q src tests
