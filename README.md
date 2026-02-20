# Legend

Legend automatically generates an architecture map of any codebase. It discovers modules, clusters files into components, and extracts technical decisions — producing a structured, navigable map of how a system is built and why.

The result is a workspace for spec-driven development: a living spec that stays in sync with your code, so teams and AI agents can plan, build, and review against a shared source of truth.

## How It Works

1. **Module Discovery** — An AI agent analyzes the codebase and classifies it into C4 Level 2 modules, shared libraries, and supporting assets.
2. **Component Discovery** — Parses a SCIP index, builds a dependency graph, and runs Leiden clustering to find cohesive groups of files (C4 Level 3 components).
3. **Description Generation** — Reads source code and extracts concrete technical decisions for each component and module.
4. **Visualization** — A Tauri desktop app renders the architecture as an interactive graph at both module and component levels.

## Tech Stack

- **Backend:** Python (FastAPI), SCIP indexing (Docker), Leiden clustering
- **Frontend:** Tauri, React, Vite, React Flow
- **LLM:** Provider-agnostic via litellm (Anthropic, OpenAI, Google)

## Prerequisites

- Python 3.10+
- Node.js + npm
- Docker (for SCIP indexing)
- An LLM API key (Anthropic, OpenAI, or Google)

## Getting Started

```bash
./start.sh
```

This sets up the Python venv, installs dependencies, builds the SCIP Docker image (first run), and launches the backend on `:8000` and the desktop app.

## Project Structure

```
backend/          # FastAPI server, pipeline steps, SCIP engine
frontend/         # Tauri + React desktop app
Natural_Language_Code/  # Architecture documentation (source of truth)
start.sh          # One-command launcher
```
