PYTHON ?= python3

.PHONY: test test-unit test-contract test-integration clean

test: test-unit test-contract test-integration

test-unit:
	$(PYTHON) -m unittest tests.test_orders tests.test_inventory -v

test-contract:
	$(PYTHON) -m unittest tests.test_contract -v

test-integration:
	$(PYTHON) -m unittest tests.test_integration -v

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
