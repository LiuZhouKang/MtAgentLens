PYTHON ?= python3

.PHONY: test audit clean

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -q

audit: test

clean:
	rm -rf __pycache__ src/unicode_guard/__pycache__ tests/__pycache__
