#!/usr/bin/env bash
set -euo pipefail

# ------------------------------
# 功能：检查并安装 libelf1, libelf-dev, zlib1g-dev, make, clang, llvm，
#       使用 nvm 安装最新 LTS Node.js，使用 rustup 安装 Rust。
#       已安装的依赖会自动跳过。
# ------------------------------

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_skip()  { echo -e "${YELLOW}[SKIP]${NC} $*"; }

# ----- 1. 系统包检查与安装 -----
REQUIRED_PKGS=("libelf1" "libelf-dev" "zlib1g-dev" "make" "clang" "llvm")
MISSING_PKGS=()

for pkg in "${REQUIRED_PKGS[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        log_skip "$pkg 已安装，跳过。"
    else
        MISSING_PKGS+=("$pkg")
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    log_info "安装缺失的系统包: ${MISSING_PKGS[*]}"
    sudo apt update
    sudo apt-get install -y --no-install-recommends "${MISSING_PKGS[@]}"
else
    log_skip "所有系统包均已安装。"
fi

# ----- 2. Node.js (通过 nvm 安装最新 LTS) -----
if command -v node &>/dev/null; then
    log_skip "Node.js 已安装: $(node --version)"
else
    log_info "Node.js 未安装，将通过 nvm 安装最新 LTS 版本。"

    # 检查 nvm 是否已安装
    if [ -s "$HOME/.nvm/nvm.sh" ]; then
        log_info "nvm 已存在，直接加载。"
        export NVM_DIR="$HOME/.nvm"
        source "$NVM_DIR/nvm.sh"
    else
        log_info "安装 nvm..."
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
        # 加载 nvm 到当前 shell
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"
        [ -s "$NVM_DIR/bash_completion" ] && source "$NVM_DIR/bash_completion"
    fi

    # 安装最新 LTS 版本
    log_info "安装 Node.js LTS..."
    nvm install --lts
    nvm use --lts
    nvm alias default 'lts/*'

    log_info "Node.js 安装完成: $(node --version)"
fi

# ----- 3. Rust (通过 rustup) -----
if command -v cargo &>/dev/null; then
    log_skip "Rust/cargo 已安装: $(cargo --version)"
else
    log_info "Rust 未安装，将通过 rustup 安装。"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    # 将 cargo 加入当前 shell 的 PATH
    source "$HOME/.cargo/env"
    log_info "Rust 安装完成: $(cargo --version)"
fi

log_info "所有依赖检查安装完毕！"