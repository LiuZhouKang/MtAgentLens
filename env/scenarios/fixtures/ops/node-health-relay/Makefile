PYTHON ?= python3

.PHONY: test audit clean

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

audit: test

clean:
	rm -rf __pycache__ tests/__pycache__ src/__pycache__
