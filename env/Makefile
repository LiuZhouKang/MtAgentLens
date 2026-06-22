ROOT := ..

build: ; $(MAKE) -C $(ROOT) build
build-frontend: ; $(MAKE) -C $(ROOT) build-frontend
build-bpf: ; $(MAKE) -C $(ROOT) build-bpf
build-rust: ; $(MAKE) -C $(ROOT) build-rust

.PHONY: env-list-scenarios
env-list-scenarios:
	@PATH="$$HOME/.local/bin:$$PATH" mtagentprism-benchmark list-scenarios

.PHONY: env-run-fast
env-run-fast:
	@if [ -z "$(SCENARIO)" ]; then \
		echo "ERROR: SCENARIO= is required"; \
		exit 1; \
	fi
	@PATH="$$HOME/.local/bin:$$PATH" mtagentprism-benchmark run --scenario $(SCENARIO) --mode process $(ARGS)

.PHONY: env-run-oneclick
env-run-oneclick:
	@if [ -z "$(SCENARIO)" ]; then \
		echo "ERROR: SCENARIO= is required"; \
		exit 1; \
	fi
	@PATH="$$HOME/.local/bin:$$PATH" mtagentprism-benchmark run --scenario $(SCENARIO) --mode tmux-live $(ARGS)

.PHONY: env-verify
env-verify:
	@if [ -z "$(RUN_DIR)" ]; then \
		echo "ERROR: RUN_DIR= is required"; \
		exit 1; \
	fi
	@PATH="$$HOME/.local/bin:$$PATH" mtagentprism-benchmark evaluate --dataset $(RUN_DIR)

.PHONY: env-smoke
env-smoke:
	@PATH="$$HOME/.local/bin:$$PATH" mtagentprism-benchmark run --scenario prompt-injection --mode process

.PHONY: env-eval
env-eval:
	@if [ -z "$(RUN_DIR)" ]; then \
		echo "ERROR: RUN_DIR= is required"; \
		exit 1; \
	fi
	@PATH="$$HOME/.local/bin:$$PATH" mtagentprism-benchmark evaluate --dataset $(RUN_DIR)

.PHONY: build build-frontend build-bpf build-rust
