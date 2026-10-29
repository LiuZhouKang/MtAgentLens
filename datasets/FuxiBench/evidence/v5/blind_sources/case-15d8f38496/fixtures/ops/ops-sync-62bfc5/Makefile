.PHONY: test lint lanes clean

test:
	@PYTHONPATH=src python3 -m unittest discover -s tests -t . -q

lanes:
	@PYTHONPATH=src python3 -c "from syncctl.lanes import report; report()"

lint:
	@PYTHONPATH=src python3 -m compileall -q src

clean:
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} +
