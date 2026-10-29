PYTHON ?= python3

.PHONY: test clean

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -v

clean:
	rm -rf __pycache__ tests/__pycache__ src/__pycache__
