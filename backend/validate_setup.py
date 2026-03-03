#!/usr/bin/env python3
"""
Setup validation script for Legend backend.

Checks Python version, dependencies, and actually imports modules to catch
lazy import failures that won't surface until runtime.

Usage:
    python validate_setup.py

Exit codes:
    0 - All checks passed
    1 - One or more checks failed
"""

import sys

# Colors for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(msg: str) -> None:
    print(f"  {GREEN}[OK]{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}[FAIL]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}[WARN]{RESET} {msg}")


def header(msg: str) -> None:
    print(f"\n{BOLD}{msg}{RESET}")


def check_python_version() -> bool:
    """Check Python >= 3.10"""
    header("Checking Python version...")
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}.{sys.version_info[2]}"

    if major < 3 or (major == 3 and minor < 10):
        fail(f"Python 3.10+ required, found {version}")
        return False

    ok(f"Python {version}")
    return True


def check_dependencies() -> bool:
    """Check all required pip packages are installed."""
    header("Checking pip dependencies...")
    from importlib.util import find_spec

    # Map of import name -> package name (for error messages)
    # Use find_spec for heavy packages to avoid slow imports
    deps_fast = {
        "fastapi": "fastapi",
        "pydantic": "pydantic",
        "uvicorn": "uvicorn",
        "google.protobuf": "protobuf",
        "networkx": "networkx",
        "igraph": "igraph",
        "leidenalg": "leidenalg",
        "pytest": "pytest",
        "httpx": "httpx",
    }

    # Heavy packages - just check they exist, don't import
    deps_heavy = {
        "litellm": "litellm",
    }

    all_ok = True

    for import_name, package_name in deps_fast.items():
        try:
            __import__(import_name)
            ok(package_name)
        except ImportError as e:
            fail(f"{package_name} - {e}")
            all_ok = False

    for import_name, package_name in deps_heavy.items():
        if find_spec(import_name):
            ok(f"{package_name} (installed)")
        else:
            fail(f"{package_name} - not installed")
            all_ok = False

    return all_ok


def check_module_imports() -> bool:
    """Check backend modules can be imported (skip heavy LLM modules)."""
    header("Checking backend module imports...")
    from importlib.util import find_spec

    # Light modules - actually import
    modules_light = [
        ("db", "Database module"),
        ("prompts", "L2 classification prompts"),
        ("component_discovery.leiden_cluster", "Leiden clustering"),
        ("component_discovery.scip_filter", "SCIP parser"),
        ("component_discovery.edge_aggregator", "Edge aggregator"),
    ]

    # Heavy modules (import litellm) - just check they exist
    modules_heavy = [
        ("component_discovery.pipeline", "Component discovery pipeline"),
        ("component_discovery.llm_client", "Component discovery LLM client"),
        ("map_descriptions.pipeline", "Map descriptions pipeline"),
        ("map_descriptions.component_describer", "Component describer"),
        ("map_descriptions.module_describer", "Module describer"),
        ("revalidation.pipeline", "Revalidation pipeline"),
    ]

    all_ok = True

    for module_name, description in modules_light:
        try:
            __import__(module_name)
            ok(description)
        except ImportError as e:
            fail(f"{description} ({module_name}) - {e}")
            all_ok = False
        except Exception as e:
            fail(f"{description} ({module_name}) - unexpected error: {e}")
            all_ok = False

    for module_name, description in modules_heavy:
        if find_spec(module_name):
            ok(f"{description} (exists)")
        else:
            fail(f"{description} ({module_name}) - not found")
            all_ok = False

    return all_ok


def check_fastapi_app() -> bool:
    """Check main.py exists and has basic structure (skip heavy import)."""
    header("Checking FastAPI app...")
    from importlib.util import find_spec
    from pathlib import Path

    # Check module exists
    if not find_spec("main"):
        fail("main module not found")
        return False

    # Check file has FastAPI app
    main_file = Path(__file__).parent / "main.py"
    if not main_file.exists():
        fail("main.py not found")
        return False

    content = main_file.read_text()
    if "app = FastAPI()" not in content:
        fail("FastAPI app not found in main.py")
        return False

    # Count routes by counting @app decorators
    route_count = content.count("@app.")
    ok(f"main.py has ~{route_count} route decorators")
    return True


def check_db_schema() -> bool:
    """Test that DB schema can be initialized in memory."""
    header("Checking database schema...")

    try:
        import sqlite3
        import db

        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        db.init_schema(conn)

        # Verify key tables exist
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r[0] for r in tables}

        required = {"modules", "components", "decisions", "pipeline_runs"}
        missing = required - table_names

        if missing:
            fail(f"Missing tables: {missing}")
            conn.close()
            return False

        ok(f"Schema initialized ({len(table_names)} tables)")
        conn.close()
        return True
    except Exception as e:
        fail(f"Schema initialization failed - {e}")
        return False


def check_leiden_smoke_test() -> bool:
    """Run Leiden clustering on a tiny 5-node graph to verify it actually works."""
    header("Running Leiden clustering smoke test...")

    try:
        import networkx as nx
        from component_discovery.leiden_cluster import run_leiden

        # Create a tiny graph: 5 nodes, 2 clusters
        # Cluster A: file1, file2, file3 (tightly connected)
        # Cluster B: file4, file5 (tightly connected)
        # Weak link between clusters
        g = nx.Graph()
        g.add_edge("src/auth/login.py", "src/auth/session.py", weight=5.0)
        g.add_edge("src/auth/login.py", "src/auth/token.py", weight=4.0)
        g.add_edge("src/auth/session.py", "src/auth/token.py", weight=3.0)
        g.add_edge("src/utils/strings.py", "src/utils/format.py", weight=5.0)
        g.add_edge("src/auth/login.py", "src/utils/strings.py", weight=0.5)  # weak cross-cluster

        result = run_leiden(g, resolution=1.0)

        if len(result) != 5:
            fail(f"Expected 5 nodes, got {len(result)}")
            return False

        # Should produce 2 clusters
        clusters = set(result.values())
        if len(clusters) < 2:
            fail(f"Expected 2 clusters, got {len(clusters)}")
            return False

        # Auth files should be in same cluster
        auth_cluster = result["src/auth/login.py"]
        if result["src/auth/session.py"] != auth_cluster or result["src/auth/token.py"] != auth_cluster:
            fail("Auth files not clustered together")
            return False

        # Utils files should be in same cluster
        utils_cluster = result["src/utils/strings.py"]
        if result["src/utils/format.py"] != utils_cluster:
            fail("Utils files not clustered together")
            return False

        # The two clusters should be different
        if auth_cluster == utils_cluster:
            fail("Auth and utils should be in different clusters")
            return False

        ok(f"5 nodes -> {len(clusters)} clusters (auth: {auth_cluster}, utils: {utils_cluster})")
        return True

    except Exception as e:
        fail(f"Leiden smoke test failed: {e}")
        return False


def check_opencode_cli() -> bool:
    """Check opencode CLI is installed (required for Part 1 pipeline)."""
    header("Checking opencode CLI...")

    import shutil
    if shutil.which("opencode"):
        ok("opencode CLI found")
        return True
    else:
        fail("opencode CLI not found - install with: npm i -g opencode-ai@latest")
        return False


def check_optional() -> None:
    """Check optional dependencies (warn but don't fail)."""
    header("Checking optional dependencies...")

    import shutil
    # Docker
    if shutil.which("docker"):
        ok("Docker (for SCIP indexing)")
    else:
        warn("Docker not found - SCIP indexing will use local binaries")


def main() -> int:
    print(f"{BOLD}Legend Backend Setup Validation{RESET}")
    print("=" * 40)

    checks = [
        check_python_version(),
        check_opencode_cli(),
        check_dependencies(),
        check_module_imports(),
        check_fastapi_app(),
        check_db_schema(),
        check_leiden_smoke_test(),
    ]

    check_optional()

    # Summary
    header("Summary")
    passed = sum(checks)
    total = len(checks)

    if all(checks):
        print(f"\n{GREEN}{BOLD}All {total} checks passed!{RESET}")
        return 0
    else:
        print(f"\n{RED}{BOLD}{total - passed}/{total} checks failed{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
