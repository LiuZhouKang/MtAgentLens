PYTHON ?= python3

.PHONY: test audit clean

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

audit: test
	@mkdir -p build
	$(PYTHON) src/account_audit.py --passwd /etc/passwd --group /etc/group \
		--scope config/review-scope.yaml --out build/accounts.json

clean:
	rm -rf build src/__pycache__ tests/__pycache__
