# Doc: Natural_Language_Code/opencode_runner/info_opencode_runner.md
# Doc: Natural_Language_Code/Frontend/info_frontend.md
"""Production uvicorn entrypoint for the Legend backend.

Used by the packaged Tauri app: the desktop shell spawns this as a sidecar
(see frontend/src-tauri/src/lib.rs) and passes the port it picked via
``--port``. Also usable standalone: ``python run_server.py --port 8123``.

Unlike ``start.sh`` (which runs ``uvicorn main:app --reload`` for development),
this runs a single-process server with no reloader — the right shape for a
bundled/PyInstaller binary.
"""

import argparse
import os
import sys

import uvicorn

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _resolve_port(cli_port: int | None) -> int:
    # Priority: --port flag, then $LEGEND_PORT, then the default.
    if cli_port is not None:
        return cli_port
    env_port = os.environ.get("LEGEND_PORT")
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            print(f"[run_server] Ignoring invalid LEGEND_PORT={env_port!r}", file=sys.stderr)
    return DEFAULT_PORT


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Legend FastAPI backend.")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind (overrides $LEGEND_PORT; default 8000).",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Host/interface to bind (default 127.0.0.1).",
    )
    args = parser.parse_args()

    port = _resolve_port(args.port)

    # Import the app object directly rather than passing "main:app" as a string so
    # this works when frozen into a single-file binary (no module import path).
    from main import app

    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
