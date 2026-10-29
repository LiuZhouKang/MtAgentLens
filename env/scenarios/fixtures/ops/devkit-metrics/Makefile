.PHONY: test clean

test:
	@PYTHONPATH=src:. python3 -m unittest discover -s tests -t . -q

clean:
	@rm -rf build
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} +
