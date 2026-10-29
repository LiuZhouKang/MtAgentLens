PYTHON ?= python3

.PHONY: validate test

validate:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tools/validate_migrations.py --root .

test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -v
