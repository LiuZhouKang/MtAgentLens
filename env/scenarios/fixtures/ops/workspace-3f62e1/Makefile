PYTHON ?= python3
REMEDIATION ?=

.PHONY: triage test verify clean

triage:
	@mkdir -p build
	$(PYTHON) tools/triage.py --logs logs --out build/triage.json

test:
	$(PYTHON) -m unittest discover -s tools/tests -t . -v

verify:
	@test -n "$(REMEDIATION)" || { echo "usage: make verify REMEDIATION=/path/to/pool.env" >&2; exit 2; }
	$(PYTHON) tools/verify_remediation.py --config "$(REMEDIATION)"

clean:
	rm -rf build tools/__pycache__ tools/tests/__pycache__
