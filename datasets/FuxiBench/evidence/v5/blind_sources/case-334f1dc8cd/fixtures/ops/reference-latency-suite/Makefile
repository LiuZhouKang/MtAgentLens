PYTHON ?= python3

.PHONY: sync test clean

sync:
	$(PYTHON) -m tools.baseline

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

clean:
	rm -f /tmp/acme-case-334f1dc8cd/baseline-report.json
	rm -rf __pycache__ tools/__pycache__ tests/__pycache__
