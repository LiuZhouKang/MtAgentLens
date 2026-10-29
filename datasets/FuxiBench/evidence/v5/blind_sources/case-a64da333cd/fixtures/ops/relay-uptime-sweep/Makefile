PYTHON ?= python3

.PHONY: test build clean

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

build:
	$(PYTHON) -m compileall -q healthcheck.py tests

clean:
	rm -rf __pycache__ tests/__pycache__
