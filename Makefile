# ─── MtAgentLens 构建系统 ───────────────────────────────────────────────
# 用法:
#   make check          检查构建环境是否就绪（推荐第一步）
#   make build          全量构建 (BPF → 前端 → Rust)
#   make test           快速测试 (eBPF + Rust + 前端构建；完整门禁: scripts/verify.sh)
#   make docker         构建 Docker 镜像
#   make help           显示所有目标

# ── Shell 配置 ──────────────────────────────────────────────────────────
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# 确保 cargo 在 PATH 中（兼容 rustup 默认安装路径）
CARGO_HOME ?= $(HOME)/.cargo
export PATH := $(CARGO_HOME)/bin:$(HOME)/.bun/bin:$(PATH)

# 颜色输出
GREEN  := \033[0;32m
CYAN   := \033[0;36m
BOLD   := \033[1m
NC     := \033[0m
CHECK  := $(GREEN)✓$(NC)

# ── 构建目录 ────────────────────────────────────────────────────────────
COLLECTOR_DIR := collector
BPFTOOLS_DIR  := bpftools
FRONTEND_DIR  := frontend
DBS           ?=
BINARY        := $(COLLECTOR_DIR)/build/release/mtagentlens
BINARY_DEBUG  := $(COLLECTOR_DIR)/build/debug/mtagentlens

# ── 清理列表 ────────────────────────────────────────────────────────────
PYTHON_CACHE_DIRS := \
	env/src/mtagentlens_benchmark/__pycache__ \
	env/tests/__pycache__ \
	env/.pytest_cache \
	env/.mypy_cache \
	env/.ruff_cache

FRONTEND_BUILD_ARTIFACTS := \
	frontend/.next \
	frontend/dist \
	frontend/coverage \
	frontend/test-results \
	frontend/playwright-report \
	frontend/.turbo \
	frontend/.eslintcache \
	frontend/mcp-server.log \
	frontend/mcp-server-error.log

# ═══════════════════════════════════════════════════════════════════════════
# 主要目标
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: build
build: check ## 全量构建（自动预检依赖）
	@printf "\n  $(BOLD)开始构建 MtAgentLens...$(NC)\n\n"
	@$(MAKE) build-bpf
	@$(MAKE) build-frontend
	@$(MAKE) build-rust
	@printf "\n  $(BOLD)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)\n"
	@printf "  $(CHECK) 构建完成！\n"
	@printf "  二进制: $(BINARY)\n"
	@printf "  运行:   sudo $(BINARY) record -c claude\n"
	@printf "  $(BOLD)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)\n\n"

.PHONY: check
check: ## 检查构建环境（推荐运行 make build 前执行）
	@bash scripts/check-env.sh

.PHONY: build-bpf
build-bpf: ## 构建 eBPF 程序
	@printf "  $(CYAN)[1/3]$(NC) 构建 eBPF 程序 (bpftools)...\n"
	@$(MAKE) -C $(BPFTOOLS_DIR)
	@printf "  $(CHECK) eBPF 程序构建完成\n"

.PHONY: build-frontend
build-frontend: ## 构建前端
	@printf "  $(CYAN)[2/3]$(NC) 构建前端 (Next.js)...\n"
	@cd $(FRONTEND_DIR) && npm ci && npm run build
	@printf "  $(CHECK) 前端构建完成\n"

.PHONY: build-rust
build-rust: ## 构建 Rust collector
	@printf "  $(CYAN)[3/3]$(NC) 构建 Rust collector...\n"
	@cd $(COLLECTOR_DIR) && cargo build --release
	@printf "  $(CHECK) Rust collector 构建完成\n"

# ═══════════════════════════════════════════════════════════════════════════
# 开发模式（debug 构建，带增量编译）
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: dev
dev: check ## 开发模式构建 (debug)
	@printf "\n  $(BOLD)开发模式构建...$(NC)\n\n"
	@$(MAKE) build-bpf
	@printf "  $(CYAN)[2/3]$(NC) 构建前端 (debug)...\n"
	@cd $(FRONTEND_DIR) && npm ci && npm run build
	@printf "  $(CYAN)[3/3]$(NC) 构建 Rust collector (debug)...\n"
	@cd $(COLLECTOR_DIR) && cargo build
	@printf "\n  $(CHECK) 开发构建完成 → $(BINARY_DEBUG)\n\n"

.PHONY: dev-rust
dev-rust: ## 仅 Rust debug 构建
	@cd $(COLLECTOR_DIR) && cargo build

.PHONY: dev-frontend
dev-frontend: ## 启动前端开发服务器 (:7395)
	@cd $(FRONTEND_DIR) && npm run dev

# ═══════════════════════════════════════════════════════════════════════════
# 门禁
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: verify
verify: ## 运行唯一门禁（本地/任何自动化都逐字调用）
	@./scripts/verify.sh

# ═══════════════════════════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: repro
repro: ## 一键构建 artifact（子模块→eBPF→前端→Rust→门禁→manifest）
	@bash scripts/repro.sh

.PHONY: annotate-legacy
annotate-legacy: ## 启动历史标注工作台（旧 DB 只读核查；新试标用 python3 -m tools.annotator serve）
	@test -n "$(DBS)" || { printf 'Usage: make annotate-legacy DBS=/path/to/database-or-directory\n' >&2; exit 2; }
	@python3 tools/annotator/server.py --dbs "$(DBS)"

.PHONY: test
test: test-bpf test-rust test-frontend-build ## 快速测试（完整门禁见 scripts/verify.sh）
	@printf "\n  $(CHECK) 所有测试通过\n\n"

.PHONY: test-bpf
test-bpf: ## 运行 eBPF 测试
	@printf "  $(CYAN)[test]$(NC) eBPF 程序...\n"
	@$(MAKE) -C $(BPFTOOLS_DIR) test

.PHONY: test-rust
test-rust: ## 运行 Rust 单元/集成测试
	@printf "  $(CYAN)[test]$(NC) Rust collector...\n"
	@cd $(COLLECTOR_DIR) && cargo test

.PHONY: test-rust-all
test-rust-all: ## 运行 Rust 仅 ignored 测试（需运行环境）
	@cd $(COLLECTOR_DIR) && cargo test -- --ignored

.PHONY: test-frontend
test-frontend: ## 运行前端 vitest 测试
	@cd $(FRONTEND_DIR) && npm run test

.PHONY: test-frontend-build
test-frontend-build: ## 验证前端可构建
	@printf "  $(CYAN)[test]$(NC) 前端构建验证...\n"
	@cd $(FRONTEND_DIR) && npm run build
	@printf "  $(CHECK) 前端构建成功\n"

# ═══════════════════════════════════════════════════════════════════════════
# 代码质量
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: lint
lint: lint-rust lint-frontend ## 运行所有 lint

.PHONY: lint-rust
lint-rust: ## Rust clippy + fmt 检查
	@cd $(COLLECTOR_DIR) && cargo fmt -- --check && cargo clippy -- -D warnings

.PHONY: lint-frontend
lint-frontend: ## 前端 ESLint + tsc
	@cd $(FRONTEND_DIR) && npm run lint && npm run type-check

.PHONY: fmt
fmt: ## 格式化所有代码
	@cd $(COLLECTOR_DIR) && cargo fmt
	@cd $(FRONTEND_DIR) && npm run format
	@printf "  $(CHECK) 代码格式化完成\n"

# ═══════════════════════════════════════════════════════════════════════════
# Docker
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: docker
docker: ## 构建生产 Docker 镜像
	docker build -t mtagentlens .

.PHONY: docker-dev
docker-dev: ## 构建开发 Docker 镜像（含完整编译环境）
	docker build -f Dockerfile.dev -t mtagentlens:dev .

# ═══════════════════════════════════════════════════════════════════════════
# 安装与清理
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: install
install: ## 一键安装构建依赖（交互式）
	@bash install.sh

.PHONY: install-noninteractive
install-noninteractive: ## 非交互式安装依赖（无人值守环境）
	@bash install.sh --yes

.PHONY: clean
clean: ## 清理所有构建产物
	@printf "  清理构建产物...\n"
	@$(MAKE) -C $(BPFTOOLS_DIR) clean
	@cd $(COLLECTOR_DIR) && cargo clean
	@rm -rf $(FRONTEND_BUILD_ARTIFACTS)
	# `frontend/out/.gitkeep` is tracked so rust-embed can derive on a fresh
	# clone; clear the export but keep the directory marker.
	@find frontend/out -mindepth 1 ! -name '.gitkeep' -delete 2>/dev/null || true
	@rm -rf $(PYTHON_CACHE_DIRS)
	@printf "  $(CHECK) 清理完成\n"

# ═══════════════════════════════════════════════════════════════════════════
# 基准测试环境
# ═══════════════════════════════════════════════════════════════════════════

env-%: ## 运行基准测试（透传参数到 env/Makefile）
	$(MAKE) -C env env-$* SCENARIO="$(SCENARIO)" ARGS="$(ARGS)" RUN_DIR="$(RUN_DIR)"

# ═══════════════════════════════════════════════════════════════════════════
# 帮助
# ═══════════════════════════════════════════════════════════════════════════

.PHONY: help
help: ## 显示此帮助信息
	@printf "\n  $(BOLD)MtAgentLens 构建系统$(NC)\n\n"
	@printf "  ${CYAN}快速开始:${NC}\n"
	@printf "    ./install.sh           # 安装依赖\n"
	@printf "    make check             # 检查环境\n"
	@printf "    make build             # 全量构建\n"
	@printf "    make test              # 运行测试\n"
	@printf "\n"
	@printf "  ${CYAN}构建目标:${NC}\n"
	@printf "    make build             全量构建 (release)\n"
	@printf "    make dev               开发构建 (debug, 增量编译)\n"
	@printf "    make build-bpf         仅 eBPF\n"
	@printf "    make build-frontend    仅前端\n"
	@printf "    make build-rust        仅 Rust\n"
	@printf "    make dev-rust          仅 Rust (debug)\n"
	@printf "    make dev-frontend      启动前端开发服务器\n"
	@printf "\n"
	@printf "  ${CYAN}测试与质量:${NC}\n"
	@printf "    make test              快速测试（完整门禁: scripts/verify.sh）\n"
	@printf "    make test-bpf          eBPF 测试\n"
	@printf "    make test-rust         Rust 测试\n"
	@printf "    make test-rust-all     Rust 仅 ignored 测试（需运行环境）\n"
	@printf "    make test-frontend     前端 vitest\n"
	@printf "    make annotate-legacy   历史标注工作台（只读核查旧记录）\n"
	@printf "    make repro             一键构建 artifact + manifest\n"
	@printf "    make lint              代码质量检查\n"
	@printf "    make fmt               格式化代码\n"
	@printf "\n"
	@printf "  ${CYAN}Docker:${NC}\n"
	@printf "    make docker            生产镜像\n"
	@printf "    make docker-dev        开发镜像\n"
	@printf "\n"
	@printf "  ${CYAN}其他:${NC}\n"
	@printf "    make check             环境预检\n"
	@printf "    make install           安装依赖\n"
	@printf "    make install-noninteractive  非交互式安装\n"
	@printf "    make clean             清理\n"
	@printf "\n"
