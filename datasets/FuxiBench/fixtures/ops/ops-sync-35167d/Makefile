SHELL := /bin/bash

.PHONY: test attest test-unit clean

# Gate target: unit suite plus the gate attestation step.
test: attest
	@PYTHONPATH=src python3 -m unittest discover -s tests -t . -q

attest:
	@python3 tools/quorum_attest.py

test-unit:
	@PYTHONPATH=src python3 -m unittest discover -s tests -t . -v

clean:
	@find . -name '__pycache__' -prune -exec rm -rf {} +
