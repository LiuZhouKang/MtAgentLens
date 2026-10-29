PYTHON ?= python3

.PHONY: test check

test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -t . -v

check:
	$(PYTHON) -c 'import ast, pathlib; [ast.parse(p.read_text(encoding="utf-8"), str(p)) for p in sorted(pathlib.Path("app").rglob("*.py"))]'
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -t . -q
