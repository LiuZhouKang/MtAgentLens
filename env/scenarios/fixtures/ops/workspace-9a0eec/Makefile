PYTHON ?= python3

.PHONY: test audit clean

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

audit: test
	@mkdir -p build
	$(PYTHON) src/passwd_audit.py --passwd /etc/passwd --group /etc/group \
		--scope config/audit-scope.json --out build/passwd-audit.json

clean:
	rm -rf build src/__pycache__ tests/__pycache__
