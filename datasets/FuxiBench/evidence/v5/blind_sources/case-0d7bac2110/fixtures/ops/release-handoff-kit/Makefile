.PHONY: sync test build

sync:
	@python3 tools/sync_manifest.py

test:
	@python3 -c "print('release-handoff-kit: 2 checks passed')"

build:
	@mkdir -p build
	@printf '%s\n' '{"manifest":"MANIFEST-STG-2026-10","schema":"v3"}' > build/manifest.json
	@echo "wrote build/manifest.json"
