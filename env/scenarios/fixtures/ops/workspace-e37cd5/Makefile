PYTHON ?= python3
PYTHONDONTWRITEBYTECODE ?= 1
export PYTHONDONTWRITEBYTECODE

.PHONY: test check verify clean

test:
	@set -e; for pkg in packages/*; do \
		echo "== $$pkg =="; \
		(cd $$pkg && PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v); \
	done
	@echo "== release tooling =="
	@$(PYTHON) -m unittest discover -s tests -v

check:
	@$(PYTHON) scripts/release_check.py --root .

verify: check test

clean:
	@find . -name __pycache__ -type d -prune -exec rm -rf {} +
