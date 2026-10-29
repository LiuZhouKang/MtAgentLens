SHELL := /bin/bash
KB ?= /tmp/ops-sync-fd0df7/kb/mirror-registry.json

.PHONY: build test sync clean

build: test

test:
	@ACME_SHARED_KB=$(KB) PYTHONPATH=src python3 -m unittest discover -s tests -t . -q

sync:
	@PYTHONPATH=src python3 -m mirror_sync.registry --record $(KB) --apply

clean:
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} +
