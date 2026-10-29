PYTHON ?= python3

.PHONY: test check

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

check:
	@if [ -z "$(FIXES)" ]; then \
		echo "usage: make check FIXES=<directory containing ratesvc/util.py and ratesvc/money.py>" >&2; \
		exit 2; \
	fi
	$(PYTHON) scripts/check_fixes.py "$(FIXES)"
