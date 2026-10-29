# MtAgentLens — Production Docker Image
# Multi-stage build: BPF → Frontend → Rust → Runtime
#
# Usage:
#   docker build -t mtagentlens .
#   docker run --rm -it --privileged --pid=host \
#       -v /sys:/sys:ro -v /usr:/usr:ro -v /lib:/lib:ro \
#       -p 7395:7395 \
#       mtagentlens record -c claude --binary-path /usr/bin/claude
#
# For non-privileged mode (requires CAP_BPF + CAP_SYS_ADMIN):
#   docker run --rm -it --cap-add=CAP_BPF --cap-add=CAP_SYS_ADMIN --pid=host \
#       -v /sys:/sys:ro -v /usr:/usr:ro -v /lib:/lib:ro \
#       -p 7395:7395 \
#       mtagentlens trace --ssl true --process true --system

# ============ Stage 1: Builder ============
FROM ubuntu:24.04 AS builder

ARG RUST_VERSION=1.92.0
ARG NODE_VERSION=22

ENV DEBIAN_FRONTEND=noninteractive \
    CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH="/usr/local/cargo/bin:${PATH}"

# System build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # eBPF build
    clang llvm libelf1 libelf-dev zlib1g-dev make pkg-config \
    # Rust toolchain
    curl ca-certificates build-essential \
    # Node.js
    xz-utils \
    # Cleanup
    && rm -rf /var/lib/apt/lists/*

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- \
    --default-toolchain ${RUST_VERSION} --profile minimal -y

# Install Node.js
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

# ── 1. Build libbpf + bpftool + BPF programs ──
RUN make -C bpftools V=1

# ── 2. Build frontend (Next.js static export) ──
RUN cd frontend && npm ci && npm run build

# ── 3. Build Rust collector (embeds BPF binaries + frontend) ──
RUN cd collector && cargo build --release

# ============ Stage 2: Runtime ============
FROM ubuntu:24.04

LABEL org.opencontainers.image.title="MtAgentLens"
LABEL org.opencontainers.image.description="eBPF-based zero-instrumentation LLM Agent observability"
LABEL org.opencontainers.image.source="https://github.com/mtagentlens/mtagentlens"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libelf1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# `.cargo/config.toml` sets `target-dir = "build"`; the binary is NOT under
# `collector/target/` (a stale path here made the image build fail).
COPY --from=builder /build/collector/build/release/mtagentlens /usr/local/bin/mtagentlens

EXPOSE 7395

ENTRYPOINT ["/usr/local/bin/mtagentlens"]
CMD ["--help"]
