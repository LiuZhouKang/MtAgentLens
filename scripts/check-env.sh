#!/usr/bin/env bash
# ─── MtAgentLens 环境预检脚本 ───────────────────────────────────────────
# 用法:
#   ./scripts/check-env.sh        # 完整检查
#   ./scripts/check-env.sh --json # JSON 格式输出（CI 集成）
#
# 退出码:
#   0 — 所有依赖就绪
#   1 — 缺少必需依赖
#   2 — 可选工具缺失（不影响构建）
#   3 — 版本过低
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

PASS=0
WARN=0
FAIL=0
JSON_MODE=0
REQUIRED_TOOLS=""
OPTIONAL_TOOLS=""
VERSION_ISSUES=""

# ─── 命令行参数 ─────────────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --json) JSON_MODE=1 ;;
        --help|-h)
            echo "Usage: $0 [--json] [--help]"
            echo ""
            echo "Check that all build dependencies for MtAgentLens are installed."
            echo ""
            echo "Options:"
            echo "  --json    Output results as JSON (for CI integration)"
            echo "  --help    Show this help"
            exit 0
            ;;
    esac
done

# ─── 辅助函数 ───────────────────────────────────────────────────────────

check_pass() {
    if [ "$JSON_MODE" -eq 0 ]; then
        printf "  ${GREEN}✓${NC} %s\n" "$1"
    fi
    PASS=$((PASS + 1))
}

check_warn() {
    if [ "$JSON_MODE" -eq 0 ]; then
        printf "  ${YELLOW}⚠${NC} %s\n" "$1"
    fi
    WARN=$((WARN + 1))
    OPTIONAL_TOOLS="${OPTIONAL_TOOLS:+$OPTIONAL_TOOLS,}\"$2\""
}

check_fail() {
    if [ "$JSON_MODE" -eq 0 ]; then
        printf "  ${RED}✗${NC} %s\n" "$1"
    fi
    FAIL=$((FAIL + 1))
    REQUIRED_TOOLS="${REQUIRED_TOOLS:+$REQUIRED_TOOLS,}\"$2\""
}

version_fail() {
    if [ "$JSON_MODE" -eq 0 ]; then
        printf "  ${RED}✗${NC} %s (found: %s, need: %s)\n" "$1" "$2" "$3"
    fi
    FAIL=$((FAIL + 1))
    VERSION_ISSUES="${VERSION_ISSUES:+$VERSION_ISSUES,}{\"tool\":\"$1\",\"found\":\"$2\",\"need\":\"$3\"}"
}

check_cmd() {
    command -v "$1" >/dev/null 2>&1
}

version_ge() {
    # Compare two version strings. Returns 0 if $1 >= $2.
    printf '%s\n%s\n' "$2" "$1" | sort -V -C 2>/dev/null
}

# ─── 系统包依赖 ─────────────────────────────────────────────────────────

check_system_pkg() {
    local pkg="$1" name="$2"
    if pkg-config --exists "$pkg" 2>/dev/null; then
        check_pass "$name (pkg-config: $pkg)"
    elif ldconfig -p 2>/dev/null | grep -q "$pkg"; then
        check_pass "$name (动态库)"
    elif [ -f "/usr/include/$pkg.h" ] || [ -f "/usr/include/$pkg/$pkg.h" ]; then
        check_pass "$name (头文件存在)"
    else
        check_fail "$name: 未安装 — 请运行 ./install.sh" "$pkg"
    fi
}

check_system_header() {
    local header="$1" name="$2"
    if [ -f "$header" ]; then
        check_pass "$name"
    else
        check_fail "$name: 未找到 $header — 请安装 libelf-dev" "libelf-dev"
    fi
}

# ─── 主检查逻辑 ─────────────────────────────────────────────────────────

main() {
    if [ "$JSON_MODE" -eq 0 ]; then
        echo ""
        printf "${BOLD}${CYAN}┌──────────────────────────────────────────────────────────┐${NC}\n"
        printf "${BOLD}${CYAN}│${NC}  ${BOLD}MtAgentLens 环境检查${NC}                                    ${BOLD}${CYAN}│${NC}\n"
        printf "${BOLD}${CYAN}└──────────────────────────────────────────────────────────┘${NC}\n"
        echo ""
    fi

    # ── 1. 系统工具 ──────────────────────────────────────────────────
    if [ "$JSON_MODE" -eq 0 ]; then
        printf "${BOLD}系统构建工具:${NC}\n"
    fi

    for tool in make gcc; do
        if check_cmd "$tool"; then
            check_pass "$tool ($(command -v "$tool"))"
        else
            check_fail "$tool: 未安装" "$tool"
        fi
    done

    # ── 2. eBPF 编译工具链 ──────────────────────────────────────────
    if [ "$JSON_MODE" -eq 0 ]; then
        echo ""
        printf "${BOLD}eBPF 编译工具链:${NC}\n"
    fi

    for tool in clang llvm-strip; do
        if check_cmd "$tool"; then
            local ver
            ver=$("$tool" --version 2>/dev/null | head -1 | grep -oP '[\d]+\.[\d]+' | head -1 || echo "unknown")
            check_pass "$tool $ver ($(command -v "$tool"))"
        else
            check_fail "$tool: 未安装 — 请运行 ./install.sh" "clang/llvm"
        fi
    done

    # libelf
    check_system_pkg "libelf" "libelf"
    check_system_header "/usr/include/gelf.h" "libelf-dev (gelf.h)"

    # zlib
    check_system_pkg "zlib" "zlib"

    # ── 3. Rust 工具链 ──────────────────────────────────────────────
    if [ "$JSON_MODE" -eq 0 ]; then
        echo ""
        printf "${BOLD}Rust 工具链:${NC}\n"
    fi

    # cargo
    local cargo_bin=""
    if check_cmd cargo; then
        cargo_bin="$(command -v cargo)"
    elif [ -f "$HOME/.cargo/bin/cargo" ]; then
        cargo_bin="$HOME/.cargo/bin/cargo"
    fi

    if [ -n "$cargo_bin" ]; then
        local rustc_bin="${cargo_bin%/cargo}/rustc"
        local rust_ver=""
        if [ -x "$rustc_bin" ]; then
            rust_ver=$("$rustc_bin" --version 2>/dev/null | grep -oP '[\d]+\.[\d]+' | head -1 || echo "unknown")
        fi
        # rust edition 2024 requires >= 1.85
        if [ -n "$rust_ver" ] && [ "$rust_ver" != "unknown" ]; then
            if version_ge "$rust_ver" "1.85"; then
                check_pass "rustc $rust_ver (edition 2024 ✓)"
            else
                version_fail "rustc" "$rust_ver" "1.85"
            fi
        else
            check_pass "rustc ($rustc_bin)"
        fi
        check_pass "cargo ($cargo_bin)"

        # cargo fmt / clippy
        if check_cmd "rustfmt" || "$rustc_bin" --version >/dev/null 2>&1 && rustup component list 2>/dev/null | grep -q 'rustfmt.*installed'; then
            check_pass "rustfmt"
        else
            check_warn "rustfmt 未安装 (建议运行: rustup component add rustfmt)" "rustfmt"
        fi
        if check_cmd "clippy-driver" || rustup component list 2>/dev/null | grep -q 'clippy.*installed'; then
            check_pass "clippy"
        else
            check_warn "clippy 未安装 (建议运行: rustup component add clippy)" "clippy"
        fi
    else
        check_fail "cargo/rustc: 未安装 — 请运行 ./install.sh 或访问 https://rustup.rs" "rust"
    fi

    # ── 4. Node.js ──────────────────────────────────────────────────
    if [ "$JSON_MODE" -eq 0 ]; then
        echo ""
        printf "${BOLD}Node.js 前端工具链:${NC}\n"
    fi

    if check_cmd node; then
        local node_ver
        node_ver=$(node --version 2>/dev/null | sed 's/^v//')
        if version_ge "$node_ver" "18"; then
            check_pass "node v$node_ver"
        else
            version_fail "node" "v$node_ver" "v18+"
        fi
    else
        check_fail "node: 未安装 — 请运行 ./install.sh" "nodejs"
    fi

    if check_cmd npm; then
        local npm_ver
        npm_ver=$(npm --version 2>/dev/null)
        check_pass "npm v$npm_ver"
    else
        check_fail "npm: 未安装" "npm"
    fi

    # ── 5. eBPF 运行时（仅警告）─────────────────────────────────────
    if [ "$JSON_MODE" -eq 0 ]; then
        echo ""
        printf "${BOLD}eBPF 运行时环境:${NC}\n"
    fi

    if [ -f /sys/kernel/btf/vmlinux ]; then
        check_pass "BTF: /sys/kernel/btf/vmlinux 存在"
    else
        kernel_ver=$(uname -r 2>/dev/null || echo "unknown")
        if version_ge "${kernel_ver%%.*}" "5"; then
            check_warn "BTF 未找到 (内核 $kernel_ver) — 需 CONFIG_DEBUG_INFO_BTF=y" "kernel-btf"
        else
            check_warn "内核 $kernel_ver < 5.0, BTF 可能不可用" "kernel-version"
        fi
    fi

    if [ -w /sys/kernel/debug ] || [ "$(id -u 2>/dev/null)" = "0" ]; then
        check_pass "eBPF debugfs 可访问"
    else
        check_warn "debugfs 不可写 — 运行时需 sudo 或 CAP_BPF" "kernel-debugfs"
    fi

    # ── 6. 可选工具 ──────────────────────────────────────────────────
    if [ "$JSON_MODE" -eq 0 ]; then
        echo ""
        printf "${BOLD}可选工具:${NC}\n"
    fi

    if check_cmd git; then
        check_pass "git ($(git --version 2>/dev/null | head -1))"
    else
        check_warn "git 未安装" "git"
    fi

    if check_cmd docker; then
        check_pass "docker ($(docker --version 2>/dev/null | head -1))"
    else
        check_warn "docker 未安装 (跳过 Docker 构建)" "docker"
    fi

    # ── 7. 项目子模块 ────────────────────────────────────────────────
    if [ "$JSON_MODE" -eq 0 ]; then
        echo ""
        printf "${BOLD}项目子模块:${NC}\n"
    fi

    local script_dir
    script_dir="$(cd "$(dirname "$0")" && pwd)"
    local project_dir="$(dirname "$script_dir")"
    local missing_gitmodules=0

    # 检查子模块目录是否存在
    for submod_dir in "libbpf/src" "bpftool/src"; do
        if [ -d "$project_dir/$submod_dir" ] && [ -n "$(ls -A "$project_dir/$submod_dir" 2>/dev/null)" ]; then
            check_pass "$submod_dir/"
        else
            check_fail "$submod_dir/: 未初始化 — 请运行: git submodule update --init --recursive" "git-submodule"
            missing_gitmodules=1
        fi
    done

    if [ "$missing_gitmodules" -eq 1 ]; then
        if [ "$JSON_MODE" -eq 0 ]; then
            echo ""
            printf "${YELLOW}  提示: 首次克隆需使用 --recursive 参数:${NC}\n"
            printf "  ${CYAN}  git clone --recursive https://github.com/mtagentlens/mtagentlens.git${NC}\n"
            printf "  ${CYAN}  # 或: cd mtagentlens && git submodule update --init --recursive${NC}\n"
        fi
    fi

    # ── 汇总 ──────────────────────────────────────────────────────────
    if [ "$JSON_MODE" -eq 1 ]; then
        printf '{\n'
        printf '  "status": "%s",\n' "$([ "$FAIL" -eq 0 ] && echo "ok" || echo "fail")"
        printf '  "pass": %d,\n' "$PASS"
        printf '  "warn": %d,\n' "$WARN"
        printf '  "fail": %d,\n' "$FAIL"
        printf '  "missing": [%s],\n' "${REQUIRED_TOOLS:-}"
        printf '  "optional_missing": [%s],\n' "${OPTIONAL_TOOLS:-}"
        printf '  "version_issues": [%s]\n' "${VERSION_ISSUES:-}"
        printf '}\n'
    else
        echo ""
        printf "${BOLD}${CYAN}────────────────────────────────────────────────────────────${NC}\n"
        printf "${BOLD}结果:${NC} "
        printf "${GREEN}${PASS} 通过${NC}, "
        printf "${YELLOW}${WARN} 警告${NC}, "
        printf "${RED}${FAIL} 失败${NC}"
        echo ""
        echo ""

        if [ "$FAIL" -gt 0 ]; then
            printf "${RED}${BOLD}✗ 缺少 $FAIL 项必需依赖。${NC}\n"
            echo ""
            echo "  快速修复（推荐）："
            printf "    ${CYAN}./install.sh${NC}\n"
            echo ""
            echo "  手动修复："
            echo "    Ubuntu/Debian: sudo apt install clang llvm libelf-dev zlib1g-dev make"
            echo "    Fedora:        sudo dnf install clang llvm elfutils-libelf-devel zlib-devel make"
            echo "    Arch:          sudo pacman -S clang llvm libelf zlib make"
            echo "    Rust:          curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
            echo "    Node.js:       curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -"
            echo ""
        elif [ "$WARN" -gt 0 ]; then
            printf "${YELLOW}${BOLD}⚠ 环境基本就绪，$WARN 项可选工具缺失（不影响构建）。${NC}\n"
            echo ""
        else
            printf "${GREEN}${BOLD}✓ 所有依赖就绪！运行 make build 开始构建。${NC}\n"
            echo ""
        fi
    fi

    return "$([ "$FAIL" -eq 0 ] && echo 0 || echo 1)"
}

main "$@"
