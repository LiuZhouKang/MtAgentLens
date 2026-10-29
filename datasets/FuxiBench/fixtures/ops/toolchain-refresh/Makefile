.PHONY: build deps check clean

build: deps check
	@echo "build completed: src/audit.py"

deps:
	@bash scripts/install_deps.sh

check:
	@python3 -m py_compile src/audit.py
	@echo "syntax check passed"

clean:
	@rm -rf .cache build src/__pycache__
