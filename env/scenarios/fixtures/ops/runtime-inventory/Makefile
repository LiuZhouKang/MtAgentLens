PY ?= python3

.PHONY: test inventory clean

test:
	$(PY) -m unittest discover -s tests -t . -v

inventory:
	$(PY) -c "import tests; print(tests.transcript_path())"

clean:
	rm -rf __pycache__ tests/__pycache__ src/__pycache__
