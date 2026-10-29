.PHONY: build deps check test clean

build: deps check
	@echo "build completed: src/sync.py"

deps:
	@bash scripts/install_deps.sh

check:
	@python3 -m py_compile src/sync.py
	@echo "syntax check passed"

test:
	@PYTHONPATH=src python3 -m unittest discover -s tests -t . -q

clean:
	@rm -rf .cache build src/__pycache__ tests/__pycache__
