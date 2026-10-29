PYTHON ?= python3

.PHONY: sync test clean

sync:
	$(PYTHON) -m tools.baseline

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

clean:
	rm -f /tmp/acme-network-anomaly/baseline-report.json
	rm -rf __pycache__ tools/__pycache__ tests/__pycache__
