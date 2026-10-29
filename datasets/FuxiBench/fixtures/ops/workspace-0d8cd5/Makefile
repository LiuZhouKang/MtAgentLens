PYTHON ?= python3

.PHONY: all test test-frontend test-api test-integration check

all: test

test: test-frontend test-api test-integration

test-frontend:
	$(PYTHON) -m unittest discover -s frontend/tests -t . -v

test-api:
	$(PYTHON) -m unittest discover -s api/tests -t . -v

test-integration:
	$(PYTHON) -m unittest discover -s integration -t . -v

check:
	$(PYTHON) integration/check_contract.py
