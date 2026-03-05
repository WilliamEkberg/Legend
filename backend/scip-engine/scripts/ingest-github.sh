#!/bin/bash
# SCIP Engine — Ingest any GitHub repo or local codebase and produce .scip files
# Usage: ./scripts/ingest-github.sh https://github.com/owner/repo [subdir]
#        ./scripts/ingest-github.sh /path/to/local/codebase
#
# This script uses Docker by default (no local dependencies needed except Docker).
# Set USE_LOCAL=1 to use a locally-built legend-indexer instead.

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Configuration
DOCKER_IMAGE="scip-engine"
USE_LOCAL="${USE_LOCAL:-0}"

# Parse arguments
SOURCE=$1
SUBDIR=${2:-""}

if [ -z "$SOURCE" ]; then
    echo -e "${RED}Error: Please provide a GitHub URL or local path${NC}"
    echo ""
    echo "Usage:"
    echo "  ./scripts/ingest-github.sh https://github.com/owner/repo [subdir]"
    echo "  ./scripts/ingest-github.sh /path/to/local/codebase"
    echo ""
    echo "Examples:"
    echo "  ./scripts/ingest-github.sh https://github.com/EPPlusSoftware/EPPlus src/EPPlus"
    echo "  ./scripts/ingest-github.sh ~/projects/my-app"
    echo ""
    echo "Environment variables:"
    echo "  USE_LOCAL=1      Use locally-built indexer instead of Docker"
    exit 1
fi

# Check if Docker is available (unless using local mode)
if [ "$USE_LOCAL" != "1" ]; then
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}Error: Docker is not installed or not in PATH${NC}"
        echo ""
        echo "Please install Docker from https://docker.com or use local mode:"
        echo "  USE_LOCAL=1 ./scripts/ingest-github.sh $SOURCE"
        exit 1
    fi

    # Check if Docker image exists
    if ! docker image inspect "$DOCKER_IMAGE" &> /dev/null; then
        echo -e "${YELLOW}Docker image '$DOCKER_IMAGE' not found.${NC}"
        echo -e "${YELLOW}Building Docker image (this may take a few minutes on first run)...${NC}"
        echo ""
        docker build -t "$DOCKER_IMAGE" "$PROJECT_ROOT/legend-indexer"
        echo ""
        echo -e "${GREEN}Docker image built successfully${NC}"
    fi
fi

# Determine if source is URL or local path
if [[ "$SOURCE" == http* ]] || [[ "$SOURCE" == git@* ]]; then
    IS_REMOTE=true
    REPO_URL="$SOURCE"
    REPO_NAME=$(basename "$REPO_URL" .git)
    echo -e "${BLUE}==> Ingesting GitHub repository: $REPO_NAME${NC}"
else
    IS_REMOTE=false
    LOCAL_PATH="$SOURCE"
    # Convert to absolute path
    if [[ "$LOCAL_PATH" != /* ]]; then
        LOCAL_PATH="$(cd "$LOCAL_PATH" 2>/dev/null && pwd)" || {
            echo -e "${RED}Error: Directory not found: $SOURCE${NC}"
            exit 1
        }
    fi
    REPO_NAME=$(basename "$LOCAL_PATH")
    echo -e "${BLUE}==> Ingesting local codebase: $REPO_NAME${NC}"
fi

# Create temp directory for GitHub repos
if [ "$IS_REMOTE" = true ]; then
    TEMP_DIR=$(mktemp -d)
    CODEBASE_PATH="$TEMP_DIR/$REPO_NAME"

    # Cleanup on exit
    cleanup() {
        if [ -d "$TEMP_DIR" ]; then
            echo -e "${YELLOW}Cleaning up temporary files...${NC}"
            rm -rf "$TEMP_DIR"
        fi
    }
    trap cleanup EXIT

    # Clone the repository
    echo -e "${YELLOW}Cloning repository...${NC}"
    git clone --depth 1 "$REPO_URL" "$CODEBASE_PATH"
    echo -e "${GREEN}Repository cloned successfully${NC}"

    # If subdirectory specified, update path
    if [ -n "$SUBDIR" ]; then
        CODEBASE_PATH="$CODEBASE_PATH/$SUBDIR"
        REPO_NAME=$(basename "$SUBDIR")
        if [ ! -d "$CODEBASE_PATH" ]; then
            echo -e "${RED}Error: Subdirectory not found: $SUBDIR${NC}"
            exit 1
        fi
        echo -e "${BLUE}Using subdirectory: $SUBDIR${NC}"
    fi
else
    CODEBASE_PATH="$LOCAL_PATH"
    if [ ! -d "$CODEBASE_PATH" ]; then
        echo -e "${RED}Error: Directory not found: $CODEBASE_PATH${NC}"
        exit 1
    fi
fi

# Create output directory
OUTPUT_DIR="$(pwd)/output"
mkdir -p "$OUTPUT_DIR"

# Check for Node.js project and install dependencies to ensure TS config resolution works
if [ -f "$CODEBASE_PATH/package.json" ]; then
    echo -e "${YELLOW}Node.js project detected. Installing dependencies for accurate indexing...${NC}"
    # Save current directory
    CURRENT_DIR=$(pwd)
    cd "$CODEBASE_PATH"

    # Try to install dependencies (ignore scripts to be safe/fast)
    if [ -f "yarn.lock" ]; then
        # Always use npx yarn to avoid conflicts with Hadoop YARN or other yarn commands
        echo -e "${BLUE}Using npx yarn (avoids Hadoop YARN conflict)...${NC}"
        npx yarn install --frozen-lockfile --ignore-scripts 2>/dev/null || \
        npx yarn install --ignore-scripts 2>/dev/null || \
        echo -e "${YELLOW}yarn install failed, continuing anyway...${NC}"
    elif [ -f "pnpm-lock.yaml" ]; then
        if command -v pnpm > /dev/null; then
            echo -e "${BLUE}Using pnpm...${NC}"
            pnpm install --frozen-lockfile --ignore-scripts --config.engine-strict=false || echo -e "${YELLOW}pnpm install failed, continuing anyway...${NC}"
        else
            echo -e "${BLUE}pnpm detected but not installed. Using npx pnpm...${NC}"
            npx pnpm install --frozen-lockfile --ignore-scripts --config.engine-strict=false || echo -e "${YELLOW}pnpm install failed, continuing anyway...${NC}"
        fi
    else
        echo -e "${BLUE}Using npm...${NC}"
        npm install --legacy-peer-deps --ignore-scripts || echo -e "${YELLOW}npm install failed, continuing anyway...${NC}"
    fi

    # Restore directory
    cd "$CURRENT_DIR"
fi

# Run the indexer
echo -e "${YELLOW}Running SCIP indexer...${NC}"

if [ "$USE_LOCAL" = "1" ]; then
    # Use local binary
    INDEXER_PATH="$PROJECT_ROOT/legend-indexer/target/release/legend-indexer"
    if [ ! -f "$INDEXER_PATH" ]; then
        echo -e "${YELLOW}Legend indexer not found. Building...${NC}"
        cd "$PROJECT_ROOT/legend-indexer"
        cargo build --release
        cd "$CURRENT_DIR"
        echo -e "${GREEN}Indexer built successfully${NC}"
    fi
    "$INDEXER_PATH" "$CODEBASE_PATH" --output "$OUTPUT_DIR"
else
    # Use Docker
    # Note: workspace is mounted read-write because indexers need to write temp files
    docker run --rm \
        -v "$CODEBASE_PATH:/workspace" \
        -v "$OUTPUT_DIR:/output" \
        -e "SCIP_MEMORY_LIMIT=${SCIP_MEMORY_LIMIT:-6144}" \
        "$DOCKER_IMAGE" \
        /workspace --output /output
fi

# Check output
SCIP_COUNT=$(find "$OUTPUT_DIR" -name "*.scip" 2>/dev/null | wc -l | tr -d ' ')

if [ "$SCIP_COUNT" -eq 0 ]; then
    echo -e "${RED}Error: No .scip files were produced${NC}"
    exit 1
fi

# Convert .scip protobuf files to JSON for pipeline consumption
echo -e "${YELLOW}Converting SCIP files to JSON...${NC}"
JSON_COUNT=0
for f in "$OUTPUT_DIR"/*.scip; do
    json_out="${f%.scip}.json"
    if docker run --rm --entrypoint scip -v "$OUTPUT_DIR:/data" "$DOCKER_IMAGE" print --json "/data/$(basename "$f")" > "$json_out" 2>/dev/null; then
        if [ -s "$json_out" ]; then
            JSON_COUNT=$((JSON_COUNT + 1))
            echo -e "  ${GREEN}✓${NC} $(basename "$json_out")"
        else
            rm -f "$json_out"
        fi
    else
        echo -e "  ${YELLOW}⚠${NC} Could not convert $(basename "$f") to JSON"
        rm -f "$json_out"
    fi
done

echo ""
echo -e "${GREEN}==================================================${NC}"
echo -e "${GREEN}  SCIP Indexing Complete!${NC}"
echo -e "${GREEN}==================================================${NC}"
echo ""
echo -e "  Codebase: ${BLUE}$REPO_NAME${NC}"
echo -e "  Output:   ${BLUE}$OUTPUT_DIR${NC}"
echo -e "  Files:    ${BLUE}$SCIP_COUNT .scip file(s)${NC}"
if [ "$JSON_COUNT" -gt 0 ]; then
    echo -e "  JSON:     ${BLUE}$JSON_COUNT .json file(s)${NC}"
fi
echo ""
echo -e "  ${YELLOW}Output files:${NC}"
ls -lh "$OUTPUT_DIR"/*.scip "$OUTPUT_DIR"/*.json 2>/dev/null || ls -lh "$OUTPUT_DIR"
echo ""
