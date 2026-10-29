SHELL := /bin/sh
PYTHON ?= python3
OUT ?= /tmp/acme-build/out
SUMMARY ?= /tmp/build-summary.txt
export PYTHONPATH := $(CURDIR)/src

.PHONY: all preflight build test quality package summary clean

all: preflight build test quality package summary

$(OUT):
	@mkdir -p "$(OUT)"

preflight: | $(OUT)
	@rm -rf "$(OUT)"
	@rm -f "$(SUMMARY)"
	@mkdir -p "$(OUT)/preflight" "$(OUT)/status"
	@$(PYTHON) scripts/check_env.py --out "$(OUT)/preflight/env.json"

build: | $(OUT)
	@mkdir -p "$(OUT)/status"
	@$(PYTHON) scripts/build.py --out-dir "$(OUT)"

test: | $(OUT)
	@mkdir -p "$(OUT)/status"
	@$(PYTHON) scripts/run_tests.py --out-dir "$(OUT)"

quality: | $(OUT)
	@mkdir -p "$(OUT)/status"
	@$(PYTHON) scripts/quality.py --out-dir "$(OUT)"

package: | $(OUT)
	@mkdir -p "$(OUT)/artifacts" "$(OUT)/status"
	@tar -czf "$(OUT)/artifacts/release.tar.gz" -C "$(OUT)/dist" .
	@cd "$(OUT)/artifacts" && sha256sum release.tar.gz > checksums.txt
	@printf 'package: PASSED\n' > "$(OUT)/status/package"
	@echo "package: PASSED ($(OUT)/artifacts/release.tar.gz)"
	@cat "$(OUT)/artifacts/checksums.txt"

summary: | $(OUT)
	@$(PYTHON) scripts/summarize.py --out-dir "$(OUT)" --summary "$(SUMMARY)"

clean:
	@rm -rf "$(OUT)"
	@rm -f "$(SUMMARY)"
	@echo "clean: removed $(OUT) and $(SUMMARY)"
