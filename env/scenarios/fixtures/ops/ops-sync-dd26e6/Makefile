.PHONY: test clean

test:
	@PYTHONPATH=src python3 -m unittest discover -s tests -t . -q

clean:
	@rm -rf src/teamsync/__pycache__ tests/__pycache__ .pytest_cache
