PYTHON ?= python3

.PHONY: test build lint clean

test:
	$(PYTHON) -m tests.run_tests

build:
	$(PYTHON) -m compileall -q src tests

lint:
	$(PYTHON) -m py_compile src/relay.py tests/run_tests.py tests/test_relay.py

clean:
	rm -rf src/__pycache__ tests/__pycache__
