#!/bin/bash
# SCIP Engine — Run ALL tests (unit, integration, robustness, lint, real-repo, end-to-end)
#
# Usage: ./scripts/run-all-tests.sh
#
# This is the exhaustive test suite. Run it after making changes to verify
# nothing is broken. It will:
#
#   1. Build the Docker images (production + test)
#   2. Run cargo tests (unit, integration, robustness)
#   3. Run clippy (lint)
#   4. Clone real test repos (supabase, ollama, zed) if not already present
#   5. Run real-repo tests against those repos
#   6. Run end-to-end indexing via the scripts
#
# Takes ~10-15 minutes on first run (cloning repos + building images).
# Subsequent runs are faster thanks to Docker layer caching and cached repos.

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
INDEXER_DIR="$PROJECT_ROOT/legend-indexer"
TEST_REPOS_DIR="$PROJECT_ROOT/.test-repos"

PASS=0
FAIL=0
SKIP=0
RESULTS=()

record() {
    local status=$1 name=$2
    if [ "$status" -eq 0 ]; then
        RESULTS+=("${GREEN}PASS${NC}  $name")
        PASS=$((PASS + 1))
    else
        RESULTS+=("${RED}FAIL${NC}  $name")
        FAIL=$((FAIL + 1))
    fi
}

record_skip() {
    local name=$1
    RESULTS+=("${YELLOW}SKIP${NC}  $name")
    SKIP=$((SKIP + 1))
}

header() {
    echo ""
    echo -e "${BOLD}${BLUE}══════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${BLUE}  $1${NC}"
    echo -e "${BOLD}${BLUE}══════════════════════════════════════════════════${NC}"
    echo ""
}

# Track total time
START_TIME=$(date +%s)

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Build Docker images
# ─────────────────────────────────────────────────────────────────────────────
header "Step 1/6: Building Docker images"

echo -e "${YELLOW}Building production image...${NC}"
if docker build -t scip-engine "$INDEXER_DIR" 2>&1; then
    record 0 "Build production image"
else
    record 1 "Build production image"
    echo -e "${RED}Production image build failed — aborting.${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}Building test image...${NC}"
if docker build -f "$INDEXER_DIR/Dockerfile.test" -t scip-engine-test "$INDEXER_DIR" 2>&1; then
    record 0 "Build test image"
else
    record 1 "Build test image"
    echo -e "${RED}Test image build failed — aborting.${NC}"
    exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Cargo tests (unit + integration + robustness)
# ─────────────────────────────────────────────────────────────────────────────
header "Step 2/6: Running cargo tests"

if docker run --rm scip-engine-test cargo test 2>&1; then
    record 0 "Cargo tests (unit + integration + robustness)"
else
    record 1 "Cargo tests (unit + integration + robustness)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Clippy lint
# ─────────────────────────────────────────────────────────────────────────────
header "Step 3/6: Running clippy"

if docker run --rm scip-engine-test cargo clippy -- -D warnings 2>&1; then
    record 0 "Clippy (lint)"
else
    record 1 "Clippy (lint)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Clone real test repos (if not already present)
# ─────────────────────────────────────────────────────────────────────────────
header "Step 4/6: Preparing real test repos"

mkdir -p "$TEST_REPOS_DIR"

clone_if_missing() {
    local name=$1 url=$2 dest="$TEST_REPOS_DIR/$1"
    if [ -d "$dest/.git" ]; then
        echo -e "  ${GREEN}$name${NC} — already cloned"
    else
        echo -e "  ${YELLOW}Cloning $name...${NC}"
        git clone --depth 1 "$url" "$dest"
        echo -e "  ${GREEN}$name${NC} — cloned"
    fi
}

clone_if_missing "supabase" "https://github.com/supabase/supabase.git"
clone_if_missing "ollama"   "https://github.com/ollama/ollama.git"
clone_if_missing "zed"      "https://github.com/zed-industries/zed.git"

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Real-repo tests
# ─────────────────────────────────────────────────────────────────────────────
header "Step 5/6: Running real-repo tests"

if docker run --rm \
    -v "$TEST_REPOS_DIR:/test-repos:ro" \
    scip-engine-test \
    cargo test --test real_repo_test -- --nocapture 2>&1; then
    record 0 "Real-repo tests (supabase, ollama, zed)"
else
    record 1 "Real-repo tests (supabase, ollama, zed)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 6: End-to-end indexing test
# ─────────────────────────────────────────────────────────────────────────────
header "Step 6/6: End-to-end indexing"

e2e_test_repo() {
    local name=$1
    local repo_path="$TEST_REPOS_DIR/$name"
    local e2e_output="$PROJECT_ROOT/output/e2e-test-$name"

    if [ ! -d "$repo_path" ]; then
        record_skip "E2E indexing: $name (repo not available)"
        return
    fi

    rm -rf "$e2e_output"
    mkdir -p "$e2e_output"

    echo -e "${YELLOW}Indexing $name end-to-end...${NC}"
    if docker run --rm \
        -v "$repo_path:/workspace" \
        -v "$e2e_output:/output" \
        scip-engine /workspace --output /output 2>&1; then

        SCIP_COUNT=$(find "$e2e_output" -name "*.scip" 2>/dev/null | wc -l | tr -d ' ')
        if [ "$SCIP_COUNT" -gt 0 ]; then
            echo -e "${GREEN}  $name: $SCIP_COUNT .scip file(s)${NC}"
            ls -lh "$e2e_output"/*.scip
            record 0 "E2E indexing: $name ($SCIP_COUNT .scip files)"
        else
            echo -e "${RED}  $name: no .scip files produced${NC}"
            record 1 "E2E indexing: $name (0 .scip files)"
        fi
    else
        record 1 "E2E indexing: $name (docker run failed)"
    fi

    rm -rf "$e2e_output"
}

e2e_test_repo "ollama"
e2e_test_repo "supabase"
e2e_test_repo "zed"

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
END_TIME=$(date +%s)
ELAPSED=$(( END_TIME - START_TIME ))
MINUTES=$(( ELAPSED / 60 ))
SECONDS=$(( ELAPSED % 60 ))

header "Results"

for r in "${RESULTS[@]}"; do
    echo -e "  $r"
done

echo ""
echo -e "  Total: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$SKIP skipped${NC}"
echo -e "  Time:  ${MINUTES}m ${SECONDS}s"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo -e "${RED}${BOLD}  SOME TESTS FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}${BOLD}  ALL TESTS PASSED${NC}"
fi
