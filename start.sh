#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$ROOT_DIR/backend/.venv"
PYTHON="python3"

# Find Python 3.10+ (needed for str | None syntax)
for candidate in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

# Check Python version is 3.10+
PY_VERSION=$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$($PYTHON -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$($PYTHON -c 'import sys; print(sys.version_info.minor)')

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "Error: Python 3.10+ required (found $PY_VERSION)"
    echo "Install a newer Python and make sure it's on PATH."
    exit 1
fi

echo "Using $PYTHON ($PY_VERSION)"

# Check Node.js is installed (needed for frontend)
if ! command -v node &>/dev/null; then
    echo "Error: Node.js not found."
    echo "  Install Node.js 18+: https://nodejs.org/"
    echo "  Or via nvm: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash"
    exit 1
fi

NODE_MAJOR=$(node -v | sed 's/v//' | cut -d. -f1)
if [ "$NODE_MAJOR" -lt 18 ]; then
    echo "Error: Node.js 18+ required (found $(node -v))"
    echo "  Update Node.js: https://nodejs.org/"
    exit 1
fi
echo "Using Node.js $(node -v)"

# Check Rust/Cargo is installed (needed for Tauri)
if ! command -v cargo &>/dev/null; then
    echo "Error: Rust/Cargo not found."
    echo "  Install Rust via rustup: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    echo "  Then restart your terminal and re-run this script."
    exit 1
fi

# Verify cargo actually works (rustup may need a default toolchain)
if ! cargo --version &>/dev/null; then
    echo "Error: Cargo found but no default toolchain configured."
    echo "  Run: rustup default stable"
    echo "  Then re-run this script."
    exit 1
fi
echo "Using Cargo $(cargo --version | cut -d' ' -f2)"

# Create venv if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
fi

# Activate venv and install deps (handle Windows vs Unix path)
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    source "$VENV_DIR/Scripts/activate"
else
    echo "Error: Could not find venv activate script"
    exit 1
fi
echo "Installing backend dependencies..."
# Parse packages from environment.yml (lines starting with "  - ")
TEMP_REQS=$(mktemp)
grep '^  - ' "$ROOT_DIR/environment.yml" | sed 's/^  - //' > "$TEMP_REQS"
pip install -q -r "$TEMP_REQS"
rm -f "$TEMP_REQS"

# Set up pre-commit hooks if not already installed
#if [ -f "$ROOT_DIR/.pre-commit-config.yaml" ] && [ ! -f "$ROOT_DIR/.git/hooks/pre-commit" ]; then
#    echo "Setting up pre-commit hooks..."
#    cd "$ROOT_DIR" && pre-commit install
#fi

# Install frontend deps if needed
if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
    echo "Installing frontend dependencies..."
    cd "$ROOT_DIR/frontend" && npm install
fi

# SCIP engine Docker image setup
# Priority: 1) Pull from GitHub Container Registry (fast), 2) Build locally (slow fallback)
SCIP_REGISTRY_IMAGE="ghcr.io/legend-llp/scip-engine:latest"
SCIP_LOCAL_IMAGE="scip-engine"

if command -v docker &>/dev/null; then
    if ! docker image inspect "$SCIP_LOCAL_IMAGE" &>/dev/null 2>&1; then
        echo "SCIP engine image not found locally."
        echo "Attempting to pull from GitHub Container Registry..."

        if docker pull "$SCIP_REGISTRY_IMAGE" 2>/dev/null; then
            echo "Successfully pulled SCIP engine image from registry."
            docker tag "$SCIP_REGISTRY_IMAGE" "$SCIP_LOCAL_IMAGE"
            echo "Tagged as '$SCIP_LOCAL_IMAGE' for local use."
        else
            echo ""
            echo "Could not pull from registry (may not exist yet or network issue)."
            echo "Building SCIP engine Docker image locally (this takes ~15-20 minutes first time)..."
            echo ""

            # Build base image first if it doesn't exist
            if ! docker image inspect legend-indexer-base:latest &>/dev/null 2>&1; then
                echo "Building base image with language runtimes..."
                docker build \
                    -f "$ROOT_DIR/backend/scip-engine/legend-indexer/Dockerfile.base" \
                    -t legend-indexer-base:latest \
                    "$ROOT_DIR/backend/scip-engine/legend-indexer"
            fi

            # Build final image
            echo "Building SCIP engine image..."
            docker build -t "$SCIP_LOCAL_IMAGE" "$ROOT_DIR/backend/scip-engine/legend-indexer"
        fi
    else
        echo "SCIP engine image found locally."
    fi
else
    echo "Warning: Docker not found. SCIP indexing will fall back to local binaries."
    echo "  Install Docker: https://docs.docker.com/get-docker/"
    echo "  Or install indexers locally: ./backend/scip-engine/scripts/install-indexers.sh"
fi

# Pre-build Tauri Rust binary (first run downloads + compiles deps, can take a while)
echo "Building Tauri Rust backend (first run may take a few minutes)..."
cd "$ROOT_DIR/frontend/src-tauri" && cargo build 2>&1
echo "Tauri Rust build complete."

# Cleanup on exit
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
    wait $BACKEND_PID $FRONTEND_PID 2>/dev/null
    echo "Done."
}
trap cleanup EXIT INT TERM

# Start backend
echo "Starting backend (FastAPI) on http://localhost:8000..."
cd "$ROOT_DIR/backend"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Start Tauri desktop app (includes Vite dev server)
echo "Starting Legend desktop app..."
cd "$ROOT_DIR/frontend"
npm run tauri:dev &
FRONTEND_PID=$!

echo ""
echo "========================================="
echo "  Legend is starting!"
echo "  Backend:  http://localhost:8000"
echo "  Desktop app launching..."
echo "  Press Ctrl+C to stop"
echo "========================================="
echo ""

# Wait for either process to exit
wait
