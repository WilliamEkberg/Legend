#!/bin/bash
# Build the SCIP engine base image with all language runtimes pre-installed.
# Run this once, or whenever you update a language runtime / add a new tool.
#
# Usage:
#   ./build-base.sh                    # Build for current platform
#   ./build-base.sh --push             # Build multi-arch and push to GHCR
#   ./build-base.sh --push --no-cache  # Force rebuild without cache
#
# Environment variables:
#   SCIP_REGISTRY   Override registry (default: ghcr.io/wuv-ogmem)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="legend-indexer-base"
TAG="latest"
REGISTRY="${SCIP_REGISTRY:-ghcr.io/legend-llp}"

echo "Building ${IMAGE_NAME}:${TAG} ..."
echo "This installs all language runtimes — takes ~10-15 min on first build."
echo "Subsequent builds use Docker layer cache."
echo ""

PUSH=false
NO_CACHE=""
for arg in "$@"; do
    case "$arg" in
        --push) PUSH=true ;;
        --no-cache) NO_CACHE="--no-cache" ;;
        --help|-h)
            echo "Usage: $0 [--push] [--no-cache]"
            echo ""
            echo "Options:"
            echo "  --push       Build multi-arch (amd64+arm64) and push to GHCR"
            echo "  --no-cache   Force rebuild without Docker cache"
            echo ""
            echo "Environment variables:"
            echo "  SCIP_REGISTRY  Override registry (default: ghcr.io/wuv-ogmem)"
            exit 0
            ;;
    esac
done

if [ "$PUSH" = true ]; then
    # Multi-arch build + push requires buildx
    echo "Building multi-arch image (linux/amd64 + linux/arm64)..."
    echo "Registry: ${REGISTRY}/${IMAGE_NAME}:${TAG}"
    echo ""

    # Ensure buildx builder exists
    if ! docker buildx inspect scip-builder &>/dev/null 2>&1; then
        echo "Creating buildx builder..."
        docker buildx create --name scip-builder --use
    else
        docker buildx use scip-builder
    fi

    docker buildx build \
        --platform linux/amd64,linux/arm64 \
        -f "${SCRIPT_DIR}/Dockerfile.base" \
        -t "${REGISTRY}/${IMAGE_NAME}:${TAG}" \
        -t "${IMAGE_NAME}:${TAG}" \
        --push \
        $NO_CACHE \
        "${SCRIPT_DIR}"

    echo ""
    echo "Done! Image pushed to:"
    echo "  ${REGISTRY}/${IMAGE_NAME}:${TAG}"
    echo ""
    echo "Pull on any machine with:"
    echo "  docker pull ${REGISTRY}/${IMAGE_NAME}:${TAG}"
else
    # Local build for current platform only
    docker build \
        -f "${SCRIPT_DIR}/Dockerfile.base" \
        -t "${IMAGE_NAME}:${TAG}" \
        $NO_CACHE \
        "${SCRIPT_DIR}"

    echo ""
    echo "Done! Base image tagged as ${IMAGE_NAME}:${TAG}"
    echo "Now build the main image with:"
    echo "  docker build -t scip-engine ${SCRIPT_DIR}"
    echo ""
    echo "To push to registry, run:"
    echo "  $0 --push"
fi
