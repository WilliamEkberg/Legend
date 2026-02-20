#!/bin/bash
# Pull SCIP engine images from GitHub Container Registry
#
# Usage:
#   ./docker-pull.sh              # Pull latest images
#   ./docker-pull.sh --force      # Force re-pull even if local image exists
#   SCIP_REGISTRY=ghcr.io/myorg ./docker-pull.sh  # Use custom registry
#
# This is much faster than building locally (~seconds vs ~20 minutes).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Registry configuration (can be overridden via environment)
REGISTRY="${SCIP_REGISTRY:-ghcr.io/williamekberg}"
BASE_IMAGE="legend-indexer-base"
FINAL_IMAGE="scip-engine"

FORCE=false
for arg in "$@"; do
    case "$arg" in
        --force|-f) FORCE=true ;;
        --help|-h)
            echo "Usage: $0 [--force]"
            echo ""
            echo "Pulls SCIP engine images from GitHub Container Registry."
            echo ""
            echo "Options:"
            echo "  --force, -f    Re-pull even if local image exists"
            echo ""
            echo "Environment variables:"
            echo "  SCIP_REGISTRY  Override registry (default: ghcr.io/williamekberg)"
            exit 0
            ;;
    esac
done

echo "=== SCIP Engine Image Pull ==="
echo "Registry: $REGISTRY"
echo ""

# Check if Docker is available
if ! command -v docker &>/dev/null; then
    echo "Error: Docker is not installed or not in PATH."
    echo "Install Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

# Pull base image
echo "Pulling base image: ${REGISTRY}/${BASE_IMAGE}:latest"
if [ "$FORCE" = true ] || ! docker image inspect "${BASE_IMAGE}:latest" &>/dev/null 2>&1; then
    if docker pull "${REGISTRY}/${BASE_IMAGE}:latest"; then
        docker tag "${REGISTRY}/${BASE_IMAGE}:latest" "${BASE_IMAGE}:latest"
        echo "✓ Base image ready"
    else
        echo "✗ Failed to pull base image"
        echo "  The image may not exist yet. Run ./build-base.sh to build locally."
        exit 1
    fi
else
    echo "✓ Base image already exists locally (use --force to re-pull)"
fi

echo ""

# Pull final image
echo "Pulling SCIP engine image: ${REGISTRY}/${FINAL_IMAGE}:latest"
if [ "$FORCE" = true ] || ! docker image inspect "${FINAL_IMAGE}:latest" &>/dev/null 2>&1; then
    if docker pull "${REGISTRY}/${FINAL_IMAGE}:latest"; then
        docker tag "${REGISTRY}/${FINAL_IMAGE}:latest" "${FINAL_IMAGE}:latest"
        echo "✓ SCIP engine image ready"
    else
        echo "✗ Failed to pull SCIP engine image"
        echo "  The image may not exist yet. Build locally with:"
        echo "  docker build -t scip-engine ."
        exit 1
    fi
else
    echo "✓ SCIP engine image already exists locally (use --force to re-pull)"
fi

echo ""
echo "=== Done ==="
echo "Images are ready. Run Legend with: ./start.sh"
