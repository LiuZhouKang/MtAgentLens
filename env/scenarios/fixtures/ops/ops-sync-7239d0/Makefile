PYTHON ?= python3

.PHONY: test check watch clean

test:
	@PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -q

check:
	@PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -q

watch:
	@PYTHONPATH=src $(PYTHON) tools/attach_observer.py

clean:
	@rm -rf .pytest_cache tests/__pycache__ src/ops_sync/__pycache__
