#!/usr/bin/env bash
# ─── MtAgentLens 一键安装脚本 ───────────────────────────────────────────
# 自动检测系统环境，安装所有构建依赖。
#
# 用法:
#   ./install.sh            # 交互式安装（推荐）
#   ./install.sh --yes      # 非交互式，自动确认
#   ./install.sh --dry-run  # 仅显示将要安装的内容，不执行
#
# 支持: Ubuntu 20.04+ / Debian 11+ / Fedora 36+ / Arch / macOS (Homebrew)

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

AUTO_YES=0
DRY_RUN=0
SUDO=""
RUST_MIN_VERSION="1.85"
NODE_MIN_VERSION="18"
NODE_TARGET_VERSION="22"

# ─── 参数解析 ───────────────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --yes|-y) AUTO_YES=1 ;;
        --dry-run|-n) DRY_RUN=1 ;;
        --help|-h)
            echo "Usage: $0 [--yes] [--dry-run] [--help]"
            echo ""
            echo "Install all build dependencies for MtAgentLens."
            echo ""
            echo "Options:"
            echo "  --yes, -y    Non-interactive mode (auto-confirm)"
            echo "  --dry-run, -n Show what would be installed, don't execute"
            echo "  --help, -h   Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg"
            exit 1
            ;;
    esac
done

# ─── 系统检测 ───────────────────────────────────────────────────────────
detect_os() {
    if [ "$(uname -s)" = "Darwin" ]; then
        echo "macos"
    elif [ -f /etc/os-release ]; then
        . /etc/os-release
        case "$ID" in
            ubuntu|debian|linuxmint|pop|elementary|zorin) echo "debian" ;;
            fedora|centos|rhel|rocky|almalinux)           echo "rhel" ;;
            arch|endeavouros|manjaro)                     echo "arch" ;;
            opensuse*)                                     echo "suse" ;;
            *)                                             echo "unknown" ;;
        esac
    else
        echo "unknown"
    fi
}

OS=$(detect_os)

if [ "$(id -u 2>/dev/null)" != "0" ] && command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
fi

# ─── 辅助函数 ───────────────────────────────────────────────────────────
log_step() { printf "\n${BOLD}${CYAN}▸${NC} ${BOLD}%s${NC}\n" "$1"; }
log_ok()   { printf "  ${GREEN}✓${NC} %s\n" "$1"; }
log_skip() { printf "  ${YELLOW}−${NC} %s (已安装)\n" "$1"; }

run_cmd() {
    local desc="$1"; shift
    printf "  ${CYAN}→${NC} %s" "$desc"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf " ${YELLOW}[dry-run: %s]${NC}\n" "$*"
        return 0
    fi
    printf "\n"
    "$@"
}

confirm() {
    if [ "$AUTO_YES" -eq 1 ] || [ "$DRY_RUN" -eq 1 ]; then return 0; fi
    local prompt="$1"
    printf "${YELLOW}%s${NC} [Y/n] " "$prompt"
    read -r answer
    case "$answer" in
        ""|[yY]|[yY][eE][sS]) return 0 ;;
        *) return 1 ;;
    esac
}

needs_rust() {
    local rustc_bin=""
    if command -v rustc >/dev/null 2>&1; then
        rustc_bin="rustc"
    elif [ -x "$HOME/.cargo/bin/rustc" ]; then
        rustc_bin="$HOME/.cargo/bin/rustc"
    else
        return 0
    fi
    local ver; ver=$("$rustc_bin" --version 2>/dev/null | grep -oP '[\d]+\.[\d]+' | head -1 || echo "0")
    if [ "$ver" != "0" ] && printf '%s\n%s\n' "$RUST_MIN_VERSION" "$ver" | sort -V -C 2>/dev/null; then
        return 1
    fi
    return 0
}

needs_node() {
    if command -v node >/dev/null 2>&1; then
        local ver; ver=$(node --version 2>/dev/null | sed 's/^v//')
        if printf '%s\n%s\n' "$NODE_MIN_VERSION" "$ver" | sort -V -C 2>/dev/null; then
            return 1
        fi
    fi
    return 0
}

# ─── 安装函数 ───────────────────────────────────────────────────────────

install_debian() {
    log_step "[1/4] 更新软件源"
    run_cmd "apt update" $SUDO apt-get update -qq

    log_step "[2/4] 安装系统构建依赖"
    local pkgs="clang llvm libelf-dev zlib1g-dev make gcc pkg-config git curl ca-certificates build-essential"
    command -v python3 >/dev/null 2>&1 || pkgs="$pkgs python3"
    run_cmd "安装编译工具链" $SUDO apt-get install -y --no-install-recommends $pkgs

    log_step "[3/4] 安装 Node.js $NODE_TARGET_VERSION.x"
    if needs_node; then
        run_cmd "通过 NodeSource 安装" \
            bash -c "curl -fsSL https://deb.nodesource.com/setup_${NODE_TARGET_VERSION}.x | $SUDO bash - && $SUDO apt-get install -y nodejs"
    else
        log_skip "Node.js $(node --version)"
    fi

    log_step "[4/4] 安装 Rust"
    if needs_rust; then
        run_cmd "通过 rustup 安装" \
            bash -c "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --component rustfmt clippy"
        [ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
    else
        log_skip "Rust $(rustc --version 2>/dev/null || echo '已安装')"
    fi
}

install_rhel() {
    log_step "[1/4] 安装系统构建依赖"
    local pkgs="clang llvm elfutils-libelf-devel zlib-devel make gcc pkg-config git curl ca-certificates"
    command -v python3 >/dev/null 2>&1 || pkgs="$pkgs python3"
    run_cmd "安装编译工具链" $SUDO dnf install -y $pkgs
    run_cmd "安装开发工具组" $SUDO dnf groupinstall -y "Development Tools" 2>/dev/null || true

    log_step "[3/4] 安装 Node.js $NODE_TARGET_VERSION.x"
    if needs_node; then
        run_cmd "通过 NodeSource 安装" \
            bash -c "curl -fsSL https://rpm.nodesource.com/setup_${NODE_TARGET_VERSION}.x | $SUDO bash - && $SUDO dnf install -y nodejs"
    else
        log_skip "Node.js $(node --version)"
    fi

    log_step "[4/4] 安装 Rust"
    if needs_rust; then
        run_cmd "通过 rustup 安装" \
            bash -c "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --component rustfmt clippy"
        [ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
    else
        log_skip "Rust $(rustc --version 2>/dev/null || echo '已安装')"
    fi
}

install_arch() {
    log_step "[1/3] 安装系统构建依赖"
    local pkgs="clang llvm libelf zlib make gcc pkg-config git curl base-devel"
    command -v python3 >/dev/null 2>&1 || pkgs="$pkgs python"
    run_cmd "安装编译工具链" $SUDO pacman -S --needed --noconfirm $pkgs

    log_step "[2/3] 安装 Node.js"
    if needs_node; then
        run_cmd "安装 nodejs npm" $SUDO pacman -S --needed --noconfirm nodejs npm
    else
        log_skip "Node.js $(node --version)"
    fi

    log_step "[3/3] 安装 Rust"
    if needs_rust; then
        if command -v rustup >/dev/null 2>&1; then
            rustup toolchain install stable --component rustfmt clippy 2>/dev/null || true
            run_cmd "设置默认工具链" rustup default stable
        else
            run_cmd "安装 rustup" $SUDO pacman -S --needed --noconfirm rustup
            run_cmd "初始化 Rust" rustup default stable
            rustup component add rustfmt clippy 2>/dev/null || true
        fi
    else
        log_skip "Rust $(rustc --version 2>/dev/null || echo '已安装')"
    fi
}

install_macos() {
    if ! command -v brew >/dev/null 2>&1; then
        echo "  Homebrew 未安装。请先安装: https://brew.sh"
        exit 1
    fi
    log_ok "Homebrew 已安装"

    log_step "[1/3] 安装构建工具"
    run_cmd "安装编译工具链" brew install llvm libelf zlib make pkg-config

    log_step "[2/3] 安装 Node.js $NODE_TARGET_VERSION"
    if needs_node; then
        run_cmd "安装 node" brew install node@${NODE_TARGET_VERSION}
    else
        log_skip "Node.js $(node --version)"
    fi

    log_step "[3/3] 安装 Rust"
    if needs_rust; then
        run_cmd "通过 rustup 安装" \
            bash -c "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --component rustfmt clippy"
    else
        log_skip "Rust $(rustc --version 2>/dev/null || echo '已安装')"
    fi

    echo ""
    echo "  ⚠ macOS 上 eBPF 程序无法编译（Linux 内核特性）。"
    echo "  → Rust + 前端可正常构建: make build-rust build-frontend"
    echo "  → 完整构建推荐 Docker: make docker-dev"
}

install_unknown() {
    echo ""
    printf "${YELLOW}无法自动识别系统。请手动安装以下依赖:${NC}\n"
    echo ""
    echo "  必需:"
    echo "    - clang + llvm (≥ 10.0)"
    echo "    - libelf + libelf-dev"
    echo "    - zlib + zlib-dev"
    echo "    - make, gcc, pkg-config"
    echo "    - Rust (≥ $RUST_MIN_VERSION): https://rustup.rs"
    echo "    - Node.js (≥ $NODE_MIN_VERSION): https://nodejs.org"
    echo ""
    echo "  安装后运行 ./scripts/check-env.sh 验证。"
    exit 1
}

# ─── 主流程 ─────────────────────────────────────────────────────────────

echo ""
printf "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════╗${NC}\n"
printf "${BOLD}${CYAN}║${NC}     ${BOLD}MtAgentLens — 一键安装构建依赖${NC}                      ${BOLD}${CYAN}║${NC}\n"
printf "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════╝${NC}\n"
echo ""
printf "  检测到系统: ${BOLD}%s${NC} (%s)\n" "$OS" "$(uname -s)"
printf "  安装模式:   ${BOLD}%s${NC}\n" "$([ "$DRY_RUN" -eq 1 ] && echo 'dry-run (仅预览)' || echo '安装')"
echo ""

if [ "$AUTO_YES" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
    confirm "继续安装? (clang, llvm, libelf, zlib, Node.js, Rust)" || { echo "已取消。"; exit 0; }
fi

export PATH="$HOME/.cargo/bin:$PATH"

case "$OS" in
    debian) install_debian ;;
    rhel)   install_rhel ;;
    arch)   install_arch ;;
    macos)  install_macos ;;
    *)      install_unknown ;;
esac

echo ""
printf "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════╗${NC}\n"
printf "${GREEN}${BOLD}║${NC}  ${BOLD}✓ 安装完成！${NC}                                          ${GREEN}${BOLD}║${NC}\n"
printf "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}\n"
echo ""

if [ "$DRY_RUN" -eq 0 ]; then
    echo "  下一步:"
    printf "    ${CYAN}./scripts/check-env.sh${NC}    # 验证环境\n"
    printf "    ${CYAN}make build${NC}                # 全量构建\n"
    echo ""
    if [ -f "$HOME/.cargo/env" ] && ! command -v cargo >/dev/null 2>&1; then
        printf "  ${YELLOW}⚠ Rust 刚安装，请先执行: source \"\$HOME/.cargo/env\"${NC}\n\n"
    fi
fi
