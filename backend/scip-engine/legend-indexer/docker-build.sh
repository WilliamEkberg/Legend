#!/bin/bash
# Build SCIP engine Docker images (base + final)
#
# Usage:
#   ./docker-build.sh                    # Build both images locally
#   ./docker-build.sh --push             # Build multi-arch and push to GHCR
#   ./docker-build.sh --final-only       # Skip base image (assumes it exists)
#   ./docker-build.sh --push --no-cache  # Force rebuild everything
#
# Environment variables:
#   SCIP_REGISTRY   Override registry (default: ghcr.io/wuv-ogmem)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_IMAGE="legend-indexer-base"
FINAL_IMAGE="scip-engine"
TAG="latest"
REGISTRY="${SCIP_REGISTRY:-ghcr.io/legend-llp}"

PUSH=false
NO_CACHE=""
FINAL_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --push) PUSH=true ;;
        --no-cache) NO_CACHE="--no-cache" ;;
        --final-only) FINAL_ONLY=true ;;
        --help|-h)
            echo "Usage: $0 [--push] [--final-only] [--no-cache]"
            echo ""
            echo "Builds SCIP engine Docker images (base + final)."
            echo ""
            echo "Options:"
            echo "  --push        Build multi-arch (amd64+arm64) and push to GHCR"
            echo "  --final-only  Skip base image build (use existing)"
            echo "  --no-cache    Force rebuild without Docker cache"
            echo ""
            echo "Environment variables:"
            echo "  SCIP_REGISTRY  Override registry (default: ghcr.io/wuv-ogmem)"
            exit 0
            ;;
    esac
done

echo "=== SCIP Engine Docker Build ==="
echo "Registry: $REGISTRY"
echo "Push: $PUSH"
echo ""

if [ "$PUSH" = true ]; then
    # Ensure buildx builder exists
    if ! docker buildx inspect scip-builder &>/dev/null 2>&1; then
        echo "Creating buildx builder..."
        docker buildx create --name scip-builder --use
    else
        docker buildx use scip-builder
    fi
fi

# ============================================
# Build base image
# ============================================
if [ "$FINAL_ONLY" = false ]; then
    echo "=== Building base image ==="
    echo "This installs all language runtimes (~10-15 min on first build)"
    echo ""

    if [ "$PUSH" = true ]; then
        docker buildx build \
            --platform linux/amd64,linux/arm64 \
            -f "${SCRIPT_DIR}/Dockerfile.base" \
            -t "${REGISTRY}/${BASE_IMAGE}:${TAG}" \
            --push \
            $NO_CACHE \
            "${SCRIPT_DIR}"
        echo "✓ Base image pushed to ${REGISTRY}/${BASE_IMAGE}:${TAG}"
    else
        docker build \
            -f "${SCRIPT_DIR}/Dockerfile.base" \
            -t "${BASE_IMAGE}:${TAG}" \
            $NO_CACHE \
            "${SCRIPT_DIR}"
        echo "✓ Base image built: ${BASE_IMAGE}:${TAG}"
    fi
    echo ""
fi

# ============================================
# Build final image
# ============================================
echo "=== Building SCIP engine image ==="

if [ "$PUSH" = true ]; then
    # For multi-arch push, we need to reference the registry base image
    # Create a temporary Dockerfile that uses the registry base
    TEMP_DOCKERFILE=$(mktemp)
    sed "s|FROM legend-indexer-base:latest|FROM ${REGISTRY}/${BASE_IMAGE}:${TAG}|g" \
        "${SCRIPT_DIR}/Dockerfile" > "$TEMP_DOCKERFILE"

    docker buildx build \
        --platform linux/amd64,linux/arm64 \
        -f "$TEMP_DOCKERFILE" \
        -t "${REGISTRY}/${FINAL_IMAGE}:${TAG}" \
        --push \
        $NO_CACHE \
        "${SCRIPT_DIR}"

    rm "$TEMP_DOCKERFILE"
    echo "✓ SCIP engine pushed to ${REGISTRY}/${FINAL_IMAGE}:${TAG}"
else
    # Check if base image exists locally
    if ! docker image inspect "${BASE_IMAGE}:${TAG}" &>/dev/null 2>&1; then
        echo "Error: Base image ${BASE_IMAGE}:${TAG} not found."
        echo "Build it first with: ./docker-build.sh (without --final-only)"
        echo "Or pull it with: ./docker-pull.sh"
        exit 1
    fi

    docker build \
        -t "${FINAL_IMAGE}:${TAG}" \
        $NO_CACHE \
        "${SCRIPT_DIR}"
    echo "✓ SCIP engine built: ${FINAL_IMAGE}:${TAG}"
fi

echo ""
echo "=== Done ==="
if [ "$PUSH" = true ]; then
    echo "Images pushed to registry. Pull with:"
    echo "  docker pull ${REGISTRY}/${FINAL_IMAGE}:${TAG}"
else
    echo "Images built locally. Run Legend with:"
    echo "  ./start.sh"
fi
