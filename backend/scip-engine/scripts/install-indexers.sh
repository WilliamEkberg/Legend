#!/usr/bin/env bash
# Install SCIP indexers for local (non-Docker) usage.
#
# This script installs the indexers needed by legend-indexer to produce
# .scip files for each language. The Docker image bundles these already,
# but for local development you need them on your PATH.
#
# Usage:
#   ./scripts/install-indexers.sh           # install all
#   ./scripts/install-indexers.sh typescript python   # install specific ones

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

installed=0
failed=0
skipped=0

install_indexer() {
    local name="$1"
    local check_cmd="$2"
    local install_cmd="$3"

    if command -v "$check_cmd" > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} $name already installed ($(command -v "$check_cmd"))"
        ((skipped++)) || true
        return
    fi

    echo -e "${BLUE}→${NC} Installing $name..."
    if eval "$install_cmd" 2>&1; then
        echo -e "${GREEN}✓${NC} $name installed"
        ((installed++)) || true
    else
        echo -e "${RED}✗${NC} $name failed to install"
        ((failed++)) || true
    fi
}

install_typescript() {
    install_indexer "scip-typescript" "scip-typescript" \
        "npm install -g @sourcegraph/scip-typescript"
}

install_python() {
    install_indexer "scip-python" "scip-python" \
        "npm install -g @sourcegraph/scip-python"
}

install_go() {
    install_indexer "scip-go" "scip-go" \
        "go install github.com/sourcegraph/scip-go@latest"
}

install_java() {
    if ! command -v coursier > /dev/null 2>&1 && ! command -v cs > /dev/null 2>&1; then
        echo -e "${YELLOW}!${NC} scip-java requires Coursier. Install from: https://get-coursier.io"
        ((skipped++)) || true
        return
    fi
    local cs_cmd="coursier"
    command -v cs > /dev/null 2>&1 && cs_cmd="cs"
    install_indexer "scip-java" "scip-java" \
        "$cs_cmd install scip-java"
}

install_dotnet() {
    if ! command -v dotnet > /dev/null 2>&1; then
        echo -e "${YELLOW}!${NC} scip-dotnet requires .NET SDK. Install from: https://dotnet.microsoft.com"
        ((skipped++)) || true
        return
    fi
    install_indexer "scip-dotnet" "scip-dotnet" \
        "dotnet tool install -g scip-dotnet"
}

install_rust() {
    install_indexer "rust-analyzer (SCIP)" "rust-analyzer" \
        "echo 'Install rust-analyzer via rustup: rustup component add rust-analyzer'"
}

# ---------- Main ----------

echo -e "${YELLOW}SCIP Indexer Installer${NC}"
echo "=============================="
echo ""

# Check prerequisites
if ! command -v node > /dev/null 2>&1; then
    echo -e "${RED}Node.js is required for TypeScript and Python indexers.${NC}"
    echo "Install from: https://nodejs.org"
    exit 1
fi

if ! command -v npm > /dev/null 2>&1; then
    echo -e "${RED}npm is required. It should come with Node.js.${NC}"
    exit 1
fi

# Determine which indexers to install
targets=("$@")
if [ ${#targets[@]} -eq 0 ]; then
    targets=("typescript" "python" "go" "java" "dotnet" "rust")
fi

for target in "${targets[@]}"; do
    case "$target" in
        typescript|ts)  install_typescript ;;
        python|py)      install_python ;;
        go|golang)      install_go ;;
        java|kotlin|scala) install_java ;;
        dotnet|csharp)  install_dotnet ;;
        rust)           install_rust ;;
        *)
            echo -e "${YELLOW}!${NC} Unknown indexer: $target"
            ((skipped++)) || true
            ;;
    esac
done

echo ""
echo "=============================="
echo -e "Installed: ${GREEN}$installed${NC}  Skipped: ${YELLOW}$skipped${NC}  Failed: ${RED}$failed${NC}"

if [ $failed -gt 0 ]; then
    exit 1
fi
