PYTHON ?= python3

.PHONY: test clean

test:
	$(PYTHON) -m unittest discover -s tests -t . -q
	$(PYTHON) -m tools.preflight

clean:
	rm -f /tmp/integration-test-report.txt
	rm -rf /tmp/acme-case-1f48d9d692
	rm -rf __pycache__ tools/__pycache__ tests/__pycache__
