PYTHON_CACHE_DIRS := \
	env/src/mtagentlens_benchmark/__pycache__ \
	env/tests/__pycache__ \
	env/.pytest_cache \
	env/.mypy_cache \
	env/.ruff_cache

FRONTEND_BUILD_ARTIFACTS := \
	frontend/.next \
	frontend/out \
	frontend/dist \
	frontend/coverage \
	frontend/test-results \
	frontend/playwright-report \
	frontend/.turbo \
	frontend/.eslintcache \
	frontend/mcp-server.log \
	frontend/mcp-server-error.log

build: build-frontend build-bpf build-rust

build-frontend: ; cd frontend && npm install && npm run build
build-bpf: ; make -C bpftools
build-rust: ; cd collector && cargo build --release

clean:
	$(MAKE) -C bpftools clean
	cd collector && cargo clean
	rm -rf $(FRONTEND_BUILD_ARTIFACTS)
	rm -rf $(PYTHON_CACHE_DIRS)

install: ; sudo apt update && sudo apt-get install -y --no-install-recommends libelf1 libelf-dev zlib1g-dev make clang llvm && (command -v node >/dev/null 2>&1 || (echo "Installing Node.js..." && curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash - && sudo apt-get install -y nodejs)) && (command -v cargo >/dev/null 2>&1 || (echo "Installing Rust..." && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y))

test: ; make -C bpftools test && cd collector && cargo test && cd ../frontend && npm run build

docker: ; docker build -t mtagentlens .

docker-dev: ; docker build -f Dockerfile.dev -t mtagentlens:dev .

env-%: ; $(MAKE) -C env env-$* SCENARIO="$(SCENARIO)" ARGS="$(ARGS)" AUTO_ATTACH="$(AUTO_ATTACH)" RUN_DIR="$(RUN_DIR)"

.PHONY: build build-frontend build-bpf build-rust clean install test docker docker-dev
