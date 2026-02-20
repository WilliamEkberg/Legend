#!/usr/bin/env bash
# Run the backend test suite.
# Usage: bash backend/tests/run.sh   (from repo root or backend dir)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$BACKEND_DIR/.venv"

cd "$BACKEND_DIR"

# Activate the project venv (created by start.sh)
if [ -d "$VENV_DIR" ]; then
    source "$VENV_DIR/bin/activate"
else
    echo "Error: Backend venv not found at $VENV_DIR"
    echo "Run start.sh first to create the virtual environment."
    exit 1
fi

# Run pytest with verbose output
python -m pytest tests/ -v --tb=short "$@"
