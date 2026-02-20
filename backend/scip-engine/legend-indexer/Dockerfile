# SCIP Engine Docker Image
# Pre-bundled with all SCIP indexers for language-agnostic codebase analysis
#
# PREREQUISITE: Build the base image first (only once, or when runtimes change):
#   ./build-base.sh
#
# Then build this image (fast — only compiles Rust code):
#   docker build -t scip-engine .
#
# Usage:
#   docker run --rm \
#     -v "/path/to/codebase:/workspace" \
#     -v "$(pwd)/output:/output" \
#     scip-engine /workspace --output /output

# ============================================
# Stage: Build dependencies (for Rust compilation)
# ============================================
FROM ubuntu:22.04 AS rust-build

ENV DEBIAN_FRONTEND=noninteractive

# Install build dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    build-essential \
    pkg-config \
    libssl-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Rust (auto-detects architecture)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y

ENV PATH="/root/.cargo/bin:${PATH}"

# Copy Cargo files first for dependency caching
WORKDIR /build
COPY Cargo.toml Cargo.lock ./

# Create dummy src to build dependencies
RUN mkdir src && echo "fn main() {}" > src/main.rs && echo "pub fn dummy() {}" > src/lib.rs

# Build dependencies only
RUN cargo build --release && rm -rf src

# Now copy actual source
COPY src ./src

# Build the actual binary
RUN touch src/main.rs src/lib.rs && cargo build --release

# ============================================
# Final stage: Base image + legend-indexer binary
# ============================================
# All language runtimes and SCIP indexers are pre-installed in the base image.
# Build it with: ./build-base.sh
FROM legend-indexer-base:latest

# Copy legend-indexer binary
COPY --from=rust-build /build/target/release/legend-indexer /usr/local/bin/legend-indexer

# Verify legend-indexer is present
RUN echo "legend-indexer: $(legend-indexer --version)"

# Entrypoint wrapper: apply SCIP_MEMORY_LIMIT at runtime, then exec legend-indexer
RUN printf '#!/bin/sh\nexport NODE_OPTIONS="--max-old-space-size=${SCIP_MEMORY_LIMIT:-6144}"\nexec legend-indexer "$@"\n' > /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["entrypoint.sh"]

# Default command shows help
CMD ["--help"]
