# Contributing to Legend

Thank you for your interest in contributing to Legend! This guide will help you get started.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). By participating, you are expected to uphold this code. Please report unacceptable behavior by opening an issue.

## How to Contribute

### Reporting Bugs

1. Check [existing issues](../../issues) to avoid duplicates.
2. Open a new issue using the **Bug** label.
3. Include:
   - Steps to reproduce
   - Expected vs. actual behavior
   - OS, Python version, Node version
   - Relevant logs or screenshots

### Suggesting Features

1. Open an issue with the **feature** label.
2. Describe the use case and why it would be valuable.
3. Be open to discussion — maintainers may suggest alternatives.

### Your First Contribution

Look for issues labeled **good-first-issue** — these are scoped, well-defined tasks meant for newcomers.

## Development Setup

### Prerequisites

- **Python 3.10+**
- **Node.js 18+** and npm
- **Rust** (for Tauri desktop app) — [install via rustup](https://rustup.rs/)
- **Docker** (recommended, for SCIP indexing)

### Getting Started

```bash
# 1. Fork the repo on GitHub, then clone your fork
git clone https://github.com/<your-username>/Legend.git
cd Legend

# 2. Run the setup script (creates venv, installs deps, builds Docker image)
./start.sh

# This starts:
#   Backend (FastAPI) → http://localhost:8000
#   Desktop app (Tauri + Vite)
```

### Manual Setup (if you prefer)

```bash
# Backend
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
cd backend && uvicorn main:app --reload

# Frontend
cd frontend && npm install
npm run tauri:dev    # Desktop app
npm run dev          # Browser-only (Vite)
```

### Running Tests

```bash
# Frontend
cd frontend && npm run build   # Type-check + build

# Backend
cd backend && python -m pytest
```

## Workflow

### 1. Fork and Branch

```bash
# Fork the repo on GitHub, then:
git clone https://github.com/<your-username>/Legend.git
cd Legend
git remote add upstream https://github.com/<org>/Legend.git

# Create a feature branch
git checkout -b feat/your-feature-name
```

**Branch naming:**
- `feat/short-description` — new features
- `fix/short-description` — bug fixes
- `docs/short-description` — documentation changes

### 2. Update Documentation First

Legend follows a **docs-first workflow**. Before writing code, update the relevant documentation in `Natural_Language_Code/`:

1. Find (or create) the `info_*.md` file for the area you're changing.
2. Describe your planned changes in the **Planned Changes** section.
3. Implement the code to match the documentation.
4. After implementation, update the doc to reflect the final state and clean up the Planned Changes section.
5. Add an entry to the **Log** section at the bottom.

See [CLAUDE.md](./CLAUDE.md) for the full documentation conventions.

> **Why?** This keeps the project's natural-language specification in sync with the code. PRs that change behavior without updating the corresponding `info_*.md` files will be asked to add documentation before merging.

### 3. Commit Messages

Write clear, concise commit messages:

```
Add force-directed layout for L2 graph view

Switched from ELK to d3-force for better handling of isolated nodes.
Updated edge routing to use straight lines instead of bezier curves.
```

- Use the imperative mood ("Add", "Fix", "Update" — not "Added", "Fixes")
- First line: short summary (under 72 characters)
- Blank line, then details if needed

### 4. Open a Pull Request

1. Push your branch to your fork.
2. Open a PR against `main`.
3. Fill in the PR template:
   - **What** does this change?
   - **Why** is it needed?
   - **How** was it tested?
   - Link to related issues (e.g., `Closes #42`)
4. Ensure CI passes (tests, linting, type checks).

### 5. Code Review

- At least **one maintainer approval** is required to merge.
- Be responsive to feedback — reviewers may request changes.
- Keep PRs focused. One feature or fix per PR. Large changes should be discussed in an issue first.

## Issue Labels

| Label | Purpose |
|-------|---------|
| `bug` | Something isn't working |
| `feature` | New feature request |
| `docs` | Documentation improvements |
| `good-first-issue` | Good for newcomers |
| `help-wanted` | Extra attention needed |
| `wontfix` | Will not be worked on |
| `duplicate` | Already exists |

## Project Structure

```
Legend/
├── backend/            # FastAPI backend (Python)
│   ├── scip-engine/    # SCIP indexing (Docker-based)
│   └── main.py         # API entry point
├── frontend/           # Tauri + React desktop app (TypeScript)
│   └── src/
├── Natural_Language_Code/  # Documentation-as-specification
│   └── <feature>/
│       └── info_*.md
├── start.sh            # One-command dev setup
├── CLAUDE.md           # AI/contributor workflow rules
└── CONTRIBUTING.md     # (this file)
```

## Style Guide

### Python (Backend)

- Follow [PEP 8](https://peps.python.org/pep-0008/).
- Use type hints (Python 3.10+ syntax: `str | None` not `Optional[str]`).
- Keep functions focused and short.

### TypeScript (Frontend)

- Run `npm run lint` before committing.
- Run `npm run build` to verify types compile.
- Use functional React components with hooks.

## Questions?

All discussion happens on [GitHub Issues](../../issues). If you're unsure about an approach, open an issue to discuss before writing code.

## License

By contributing to Legend, you agree that your contributions will be licensed under the [GNU Affero General Public License v3.0 (AGPL-3.0)](https://www.gnu.org/licenses/agpl-3.0.html).
