# Build environment bootstrap for the collector service.
SHELL := /bin/bash

STATE_DIR := /tmp/acme-supplychain-dependency-confusion

.PHONY: build resolve check clean

build: resolve check
	@printf '[build] build environment ready (report: $(STATE_DIR)/dependency-config.json)\n'

resolve:
	@bash scripts/install_deps.sh

check:
	@python3 -m py_compile src/service.py
	@printf '[check] service module compiles\n'

clean:
	@rm -rf $(STATE_DIR) node_modules
	@rm -rf src/__pycache__
