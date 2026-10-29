.PHONY: test lint clean

test:
	@PYTHONPATH=src python3 -m unittest discover -s tests -t . -q

lint:
	@PYTHONPATH=src python3 -m compileall -q src

clean:
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} +
