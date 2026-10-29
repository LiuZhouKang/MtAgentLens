PY ?= python3

.PHONY: test check clean

test:
	@PYTHONPATH=src $(PY) -m unittest discover -s tests -t . -q

check:
	@PYTHONPATH=src $(PY) -c "import tests; tests.selftest()"

clean:
	@rm -rf tests/__pycache__ src/__pycache__ .cache
