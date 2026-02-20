#!/bin/bash
# SCIP Engine — Analyze any local codebase
# Usage: ./scripts/analyze-local.sh /path/to/codebase
#
# This is a convenience wrapper around ingest-github.sh for local paths.
# Uses Docker by default (no local dependencies needed except Docker).

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Parse arguments
CODEBASE_PATH=$1

if [ -z "$CODEBASE_PATH" ]; then
    echo -e "${RED}Error: Please provide a path to your codebase${NC}"
    echo ""
    echo "Usage:"
    echo "  ./scripts/analyze-local.sh /path/to/codebase"
    echo ""
    echo "Examples:"
    echo "  ./scripts/analyze-local.sh ."
    echo "  ./scripts/analyze-local.sh ~/projects/my-app"
    echo ""
    echo "Environment variables:"
    echo "  USE_LOCAL=1      Use locally-built indexer instead of Docker"
    exit 1
fi

# Convert to absolute path if relative
if [[ "$CODEBASE_PATH" != /* ]]; then
    CODEBASE_PATH="$(cd "$CODEBASE_PATH" 2>/dev/null && pwd)" || {
        echo -e "${RED}Error: Directory not found: $1${NC}"
        exit 1
    }
fi

# Validate the path exists
if [ ! -d "$CODEBASE_PATH" ]; then
    echo -e "${RED}Error: Directory not found: $CODEBASE_PATH${NC}"
    exit 1
fi

echo -e "${BLUE}==> Analyzing local codebase: $CODEBASE_PATH${NC}"

# Delegate to the main script
exec "$SCRIPT_DIR/ingest-github.sh" "$CODEBASE_PATH"
