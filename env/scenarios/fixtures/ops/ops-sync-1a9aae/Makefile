.PHONY: test replay clean

test:
	@PYTHONPATH=src python3 -m unittest discover -s tests -t . -q

replay: test

clean:
	@rm -rf build __pycache__ src/__pycache__ tests/__pycache__
