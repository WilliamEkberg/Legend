# Doc: Natural_Language_Code/opencode_runner/info_opencode_runner.md
# Doc: Natural_Language_Code/Frontend/info_frontend.md

import asyncio
import json
import os
import queue as _queue
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import sqlite3

# Fix Windows asyncio subprocess issue with uvicorn --reload
# Without this, asyncio.create_subprocess_exec raises NotImplementedError
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import db
from prompts import (
    modules_system_prompt, edges_system_prompt,
    modules_variables_prompt, edges_variables_prompt,
    MODULES_FILENAME, EDGES_FILENAME,
    system_prompt, variables_prompt,
)

app = FastAPI()

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "output"
DB_PATH = OUTPUT_DIR / "legend.db"


@app.on_event("startup")
def _ensure_db():
    """Create the output directory and initialise the database if missing."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    conn = db.connect(str(DB_PATH))
    db.init_schema(conn)
    db.close(conn)
SCIP_ENGINE_DIR = Path(__file__).resolve().parent / "scip-engine"


# Docker registry configuration
SCIP_REGISTRY = os.environ.get("SCIP_REGISTRY", "ghcr.io/williamekberg")
SCIP_REGISTRY_IMAGE = f"{SCIP_REGISTRY}/scip-engine:latest"
SCIP_LOCAL_IMAGE = "scip-engine"


def _docker_image_exists(image: str = SCIP_LOCAL_IMAGE) -> bool:
    """Check if the Docker image is available locally."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _pull_docker_image() -> bool:
    """Try to pull the SCIP engine image from the registry.

    Returns True if successful, False otherwise.
    """
    try:
        result = subprocess.run(
            ["docker", "pull", SCIP_REGISTRY_IMAGE],
            capture_output=True, timeout=300,  # 5 min timeout for pull
        )
        if result.returncode == 0:
            # Tag as local image name
            subprocess.run(
                ["docker", "tag", SCIP_REGISTRY_IMAGE, SCIP_LOCAL_IMAGE],
                capture_output=True, timeout=10,
            )
            return True
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _ensure_docker_image() -> bool:
    """Ensure the SCIP engine Docker image is available.

    Tries local image first, then pulls from registry if needed.
    Returns True if image is available, False otherwise.
    """
    if _docker_image_exists():
        return True
    # Try to pull from registry
    return _pull_docker_image()


def _get_scip_cmd(source_dir: str) -> tuple[list[str], str]:
    """Return (command, description) for the available SCIP indexer.

    Priority: Docker (all indexers bundled) > local binary > script fallback.
    Set SCIP_LOCAL=1 to force local binary.
    """
    # Resolve to canonical absolute path (fixes macOS Docker mount issues)
    source_path = Path(source_dir).resolve()
    if not source_path.is_dir():
        raise ValueError(f"Source directory does not exist: {source_dir}")
    source_dir = str(source_path)

    indexer_binary = SCIP_ENGINE_DIR / "legend-indexer" / "target" / "release" / "legend-indexer"
    scip_script = SCIP_ENGINE_DIR / "scripts" / "analyze-local.sh"
    force_local = os.environ.get("SCIP_LOCAL") == "1"

    # Docker preferred (has all indexers + runtimes bundled)
    # Use --mount instead of -v: fails loudly if source path doesn't exist (macOS -v silently mounts empty dir)
    if not force_local and _ensure_docker_image():
        return ["docker", "run", "--rm",
                "--mount", f"type=bind,source={source_dir},target=/workspace",
                "--mount", f"type=bind,source={str(OUTPUT_DIR)},target=/output",
                SCIP_LOCAL_IMAGE, "/workspace", "--output", "/output"], "Docker (scip-engine image)"

    # Local binary fallback
    if indexer_binary.exists():
        return [str(indexer_binary), source_dir, "--output", str(OUTPUT_DIR)], "local legend-indexer binary"
    elif scip_script.exists():
        return ["bash", str(scip_script), source_dir, str(OUTPUT_DIR)], "analyze-local.sh script"
    else:
        return ["docker", "run", "--rm",
                "--mount", f"type=bind,source={source_dir},target=/workspace",
                "--mount", f"type=bind,source={str(OUTPUT_DIR)},target=/output",
                SCIP_LOCAL_IMAGE, "/workspace", "--output", "/output"], "Docker (scip-engine image)"


SCIP_DOCKER_MAX_RETRIES = 3
SCIP_DOCKER_RETRY_DELAY = 2  # seconds
SCIP_DOCKER_MAX_RETRIES = 3  # max attempts for Docker mount issues


def _is_docker_scip_cmd(cmd: list[str]) -> bool:
    """Check if the SCIP command uses Docker (may need retry for mount issues)."""
    return len(cmd) > 0 and cmd[0] == "docker"


PROVIDER_ENV_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}


def _get_opencode_executable() -> str:
    """Get the full path to the opencode executable.

    On Windows, npm-installed global packages create .cmd files that need
    to be explicitly specified for asyncio.create_subprocess_exec.
    """
    opencode_path = shutil.which("opencode")
    if opencode_path:
        return opencode_path
    # Fallback - let subprocess try to find it
    return "opencode"


def build_prompt(provider: str, model: str, repo_path: str | None = None) -> str:
    """Build the full prompt combining system prompt and variables."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    target_dir = repo_path if repo_path else str(PROJECT_DIR)

    sys_prompt = system_prompt()
    var_prompt = variables_prompt(
        project_dir=target_dir,
        output_dir=str(OUTPUT_DIR),
        cwd=str(OUTPUT_DIR),
        provider=provider,
        model=model,
        timestamp=timestamp,
    )

    # Combine system prompt and variables prompt
    return f"{sys_prompt}\n\n---\n\n{var_prompt}"


def ingest_l2_output(conn, json_path: str, run_id: int) -> None:
    """
    Ingest L2 classification JSON output into the database.

    Step 1: Insert all modules and build ID map (JSON string ID -> DB integer ID)
    Step 2: Collect all edges into a dict keyed by (source_id, target_id, edge_type),
            deduplicating between `relationships` (explicit, higher priority) and
            `consumedBy` (implicit, lower priority — only fills gaps not covered by
            the consumer module's own relationships array).
    Step 3: Insert all unique edges.

    Doc: Natural_Language_Code/opencode_runner/info_opencode_runner_L2_clustering.md (lines 194-238)
    """
    with open(json_path, 'r') as f:
        json_data = json.load(f)

    modules = json_data.get("modules", [])
    name_by_id = {e["id"]: e["name"] for e in modules}

    # Step 1: Insert modules and build ID map
    id_map = {}  # {json_string_id: db_integer_id}

    for entry in modules:
        module_id = db.add_module(
            conn,
            name=entry["name"],
            classification=entry.get("classification", "module"),
            type=entry.get("type"),
            technology=entry.get("technology"),
            source_origin=entry.get("sourceOrigin"),
            deployment_target=entry.get("deploymentTarget"),
            run_id=run_id
        )

        directories = entry.get("directories", [])
        if directories:
            db.add_module_directories(conn, module_id, directories)

        id_map[entry["id"]] = module_id

    # Step 2: Collect unique edges — (source_id, target_id, edge_type) -> metadata
    # relationships entries are processed first and take priority over consumedBy.
    edges = {}

    for entry in modules:
        source_id = id_map[entry["id"]]
        source_name = entry["name"]

        relationships = entry.get("relationships", [])

        # Handle old format (dict) for backwards compatibility
        if isinstance(relationships, dict):
            converted = []
            for t in relationships.get("dependsOnModules", []):
                converted.append({"targetModuleId": t, "type": "depends_on", "description": f"Depends on {t}"})
            for t in relationships.get("usesDataStores", []):
                converted.append({"targetModuleId": t, "type": "uses_data_store", "description": f"Stores data in {t}"})
            for comm in relationships.get("communicatesVia", []):
                converted.append({"targetModuleId": comm.get("targetModuleId"), "type": "communicates_via",
                                   "protocol": comm.get("protocol"), "description": comm.get("description")})
            relationships = converted

        for rel in relationships:
            target_str_id = rel.get("targetModuleId")
            if not target_str_id or target_str_id not in id_map:
                continue

            target_id = id_map[target_str_id]
            target_name = name_by_id.get(target_str_id, target_str_id)
            rel_type = rel.get("type", "depends_on")
            description = rel.get("description", "")

            if rel_type == "communicates_via":
                protocol = rel.get("protocol", "")
                label = f"{protocol}: {target_name}" if protocol else f"calls {target_name}"
                metadata = json.dumps({"label": label, "protocol": protocol, "description": description})
            elif rel_type == "uses_data_store":
                label = f"stores data in {target_name}"
                metadata = json.dumps({"label": label, "description": description or f"Connects to {target_name}"})
            else:  # depends_on
                label = f"uses {target_name}"
                metadata = json.dumps({"label": label, "description": description or f"{source_name} depends on {target_name}"})

            key = (source_id, target_id, rel_type)
            if key not in edges:
                edges[key] = metadata

    # consumedBy: fills in edges for consumers that didn't declare the dependency themselves
    for entry in modules:
        if entry.get("classification") != "shared-library":
            continue

        library_id = id_map[entry["id"]]
        library_name = entry["name"]

        for consumer_str_id in entry.get("consumedBy", []):
            if consumer_str_id not in id_map:
                continue

            consumer_id = id_map[consumer_str_id]
            consumer_name = name_by_id.get(consumer_str_id, consumer_str_id)
            key = (consumer_id, library_id, "depends_on")
            if key not in edges:
                edges[key] = json.dumps({
                    "label": f"uses {library_name}",
                    "description": f"{consumer_name} imports {library_name}"
                })

    # Step 3: Insert all unique edges
    for (source_id, target_id, edge_type), metadata in edges.items():
        db.add_module_edge(conn, source_id, target_id, edge_type, 1.0, metadata, run_id)


def ingest_l2_modules(conn, json_path: str, run_id: int) -> dict:
    """
    Ingest the Step 1 modules JSON into the database.

    Returns id_map: {json_string_id -> db_integer_id} for use in ingest_l2_edges.
    """
    with open(json_path, 'r') as f:
        json_data = json.load(f)

    id_map = {}
    for entry in json_data.get("modules", []):
        module_id = db.add_module(
            conn,
            name=entry["name"],
            classification=entry.get("classification", "module"),
            type=entry.get("type"),
            technology=entry.get("technology"),
            source_origin=entry.get("sourceOrigin"),
            deployment_target=entry.get("deploymentTarget"),
            run_id=run_id,
        )
        directories = entry.get("directories", [])
        if directories:
            db.add_module_directories(conn, module_id, directories)
        id_map[entry["id"]] = module_id

    return id_map


def ingest_l2_edges(conn, json_path: str, id_map: dict, run_id: int) -> tuple[int, list]:
    """
    Ingest the Step 2 edges JSON into the database.

    Uses id_map (from ingest_l2_modules) to resolve string IDs -> DB integer IDs.
    Returns the number of edges actually written.
    """
    with open(json_path, 'r') as f:
        json_data = json.load(f)

    def _get_name(db_id: int) -> str:
        row = conn.execute("SELECT name FROM modules WHERE id = ?", (db_id,)).fetchone()
        return row["name"] if row else str(db_id)

    seen_edges: set = set()
    skipped_ids: list = []

    # Direct edges
    for edge in json_data.get("edges", []):
        source_str = edge.get("sourceId")
        target_str = edge.get("targetId")
        source_id = id_map.get(source_str)
        target_id = id_map.get(target_str)
        if not source_id or not target_id or source_id == target_id:
            if source_str and target_str:
                skipped_ids.append(f"{source_str}→{target_str}")
            continue

        edge_type = edge.get("type", "depends_on")
        description = edge.get("description", "")
        protocol = edge.get("protocol", "")
        target_name = _get_name(target_id)

        if edge_type == "communicates_via":
            label = f"{protocol}: {target_name}" if protocol else f"calls {target_name}"
            metadata = json.dumps({"label": label, "protocol": protocol, "description": description})
        elif edge_type == "uses_data_store":
            label = f"stores data in {target_name}"
            metadata = json.dumps({"label": label, "description": description})
        else:  # depends_on
            label = f"uses {target_name}"
            metadata = json.dumps({"label": label, "description": description})

        key = (source_id, target_id, edge_type)
        if key not in seen_edges:
            seen_edges.add(key)
            db.add_module_edge(conn, source_id, target_id, edge_type, 1.0, metadata, run_id)

    # consumedBy — shared library consumer relationships
    for cb in json_data.get("consumedBy", []):
        library_str_id = cb.get("libraryId")
        library_id = id_map.get(library_str_id)
        if not library_id:
            continue
        library_name = _get_name(library_id)

        for consumer_str_id in cb.get("consumerIds", []):
            consumer_id = id_map.get(consumer_str_id)
            if not consumer_id:
                continue
            key = (consumer_id, library_id, "depends_on")
            if key not in seen_edges:
                seen_edges.add(key)
                db.add_module_edge(
                    conn, consumer_id, library_id, "depends_on", 1.0,
                    json.dumps({"label": f"uses {library_name}"}),
                    run_id,
                )

    return len(seen_edges), skipped_ids


class RunRequest(BaseModel):
    api_key: str
    provider: str = "anthropic"
    model: str | None = None


class RunResponse(BaseModel):
    success: bool
    output: str
    error: str


@app.post("/api/run", response_model=RunResponse)
async def run_opencode(req: RunRequest):
    env_var = PROVIDER_ENV_MAP.get(req.provider)
    if not env_var:
        return RunResponse(
            success=False,
            output="",
            error=f"Unknown provider: {req.provider}. Supported: {', '.join(PROVIDER_ENV_MAP)}",
        )

    model = req.model or f"{req.provider}/claude-sonnet-4-20250514"
    if req.provider == "anthropic" and not req.model:
        model = "anthropic/claude-sonnet-4-20250514"
    elif req.provider == "openai" and not req.model:
        model = "openai/gpt-4o"

    prompt = build_prompt(req.provider, model)

    env = {**os.environ, env_var: req.api_key}

    OUTPUT_DIR.mkdir(exist_ok=True)

    cmd = [_get_opencode_executable(), "run", "--agent", "build", "-m", model]

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(OUTPUT_DIR),
            env=env,
        )

        # If successful, ingest the output into the database
        if result.returncode == 0:
            # Find the most recent c4_level2_*.json file in OUTPUT_DIR
            json_files = sorted(OUTPUT_DIR.glob("c4_level2_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

            if json_files:
                # Connect to database
                conn = db.connect(str(DB_PATH))
                db.init_schema(conn)

                # Clear old modules before ingesting new data (re-run scenario)
                db.clear_modules(conn)

                # Start pipeline run
                run_id = db.start_pipeline_run(conn, "opencode_l2_classification")

                try:
                    # Ingest the JSON output
                    ingest_l2_output(conn, str(json_files[0]), run_id)

                    # Complete pipeline run
                    db.complete_pipeline_run(conn, run_id, "completed")
                except Exception as e:
                    # Mark pipeline run as failed
                    db.complete_pipeline_run(conn, run_id, "failed")
                    db.close(conn)
                    return RunResponse(
                        success=False,
                        output=result.stdout,
                        error=f"Database ingestion failed: {str(e)}",
                    )

                db.close(conn)

        return RunResponse(
            success=result.returncode == 0,
            output=result.stdout,
            error=result.stderr,
        )
    except FileNotFoundError:
        return RunResponse(
            success=False,
            output="",
            error="opencode CLI not found. Install with: npm i -g opencode-ai@latest",
        )
    except subprocess.TimeoutExpired:
        return RunResponse(
            success=False,
            output="",
            error="Command timed out after 120 seconds.",
        )


# ---------------------------------------------------------------------------
# Map endpoint
# ---------------------------------------------------------------------------

@app.get("/api/map")
async def get_map():
    """Return the full architecture map as JSON for the frontend graph view."""
    if not DB_PATH.exists():
        return {"modules": [], "module_edges": [], "component_edges": []}

    conn = db.connect(str(DB_PATH))
    try:
        data = db.export_full_map(conn)
        return data
    finally:
        db.close(conn)


@app.post("/api/map/import")
async def import_map(request: Request):
    """Import a full architecture map from exported JSON."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Accept either wrapped format {version, map: {...}} or raw MapData
    if "map" in body and "modules" not in body:
        map_data = body["map"]
    else:
        map_data = body

    if "modules" not in map_data:
        raise HTTPException(status_code=400, detail="Missing 'modules' key in import data")

    OUTPUT_DIR.mkdir(exist_ok=True)
    conn = db.connect(str(DB_PATH))
    db.init_schema(conn)
    try:
        summary = db.import_full_map(conn, map_data)
        return {"ok": True, "summary": summary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")
    finally:
        db.close(conn)


# ---------------------------------------------------------------------------
# Map Editor: decision CRUD + change tracking
# Doc: Natural_Language_Code/research_agent/info_map_editor.md
# ---------------------------------------------------------------------------

class DecisionUpdate(BaseModel):
    text: str | None = None
    category: str | None = None
    detail: str | None = None


class DecisionCreate(BaseModel):
    text: str
    category: str
    module_id: int | None = None
    component_id: int | None = None
    detail: str | None = None


def _current_baseline_id(conn) -> int | None:
    baseline = db.get_current_baseline(conn)
    return baseline["id"] if baseline else None


def _resolve_decision_context(conn, decision: dict) -> tuple[int | None, int | None]:
    """Return (module_id, component_id) for a decision row.

    Decisions belong to either a module or a component.  For component
    decisions we also resolve the parent module_id.
    """
    mid = decision.get("module_id")
    cid = decision.get("component_id")
    if cid and not mid:
        row = conn.execute("SELECT module_id FROM components WHERE id = ?", (cid,)).fetchone()
        if row:
            mid = row["module_id"]
    return mid, cid


@app.patch("/api/decisions/{decision_id}")
async def patch_decision(decision_id: int, body: DecisionUpdate):
    """Edit a decision's text and/or category. Creates a change record."""
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database not found")
    conn = db.connect(str(DB_PATH))
    try:
        old = db.get_decision(conn, decision_id)
        if old is None:
            raise HTTPException(status_code=404, detail="Decision not found")

        updates: dict = {"source": "human"}
        if body.text is not None:
            updates["text"] = body.text
        if body.category is not None:
            updates["category"] = body.category
        if body.detail is not None:
            updates["detail"] = body.detail
        db.update_decision(conn, decision_id, **updates)

        mid, cid = _resolve_decision_context(conn, old)
        new = db.get_decision(conn, decision_id)
        db.add_change_record(
            conn,
            entity_type="decision",
            entity_id=decision_id,
            action="edit",
            old_value=json.dumps({"category": old["category"], "text": old["text"]}),
            new_value=json.dumps({"category": new["category"], "text": new["text"]}),
            origin="human",
            baseline_id=_current_baseline_id(conn),
            module_id=mid,
            component_id=cid,
        )
        return {"ok": True}
    finally:
        db.close(conn)


@app.post("/api/decisions")
async def create_decision(body: DecisionCreate):
    """Add a new decision to a component or module. Creates a change record."""
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database not found")
    conn = db.connect(str(DB_PATH))
    try:
        new_id = db.add_decision(
            conn,
            body.category,
            body.text,
            module_id=body.module_id,
            component_id=body.component_id,
            source="human",
            detail=body.detail,
        )
        if new_id is None:
            raise HTTPException(status_code=500, detail="Insert returned no ID")

        mid = body.module_id
        cid = body.component_id
        if cid and not mid:
            row = conn.execute("SELECT module_id FROM components WHERE id = ?", (cid,)).fetchone()
            if row:
                mid = row["module_id"]

        db.add_change_record(
            conn,
            entity_type="decision",
            entity_id=new_id,
            action="add",
            old_value=None,
            new_value=json.dumps({"category": body.category, "text": body.text}),
            origin="human",
            baseline_id=_current_baseline_id(conn),
            module_id=mid,
            component_id=cid,
        )
        return {"id": new_id}
    finally:
        db.close(conn)


@app.delete("/api/decisions/{decision_id}")
async def delete_decision_endpoint(decision_id: int):
    """Remove a decision. Creates a change record before deleting."""
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database not found")
    conn = db.connect(str(DB_PATH))
    try:
        old = db.get_decision(conn, decision_id)
        if old is None:
            raise HTTPException(status_code=404, detail="Decision not found")

        mid, cid = _resolve_decision_context(conn, old)
        db.add_change_record(
            conn,
            entity_type="decision",
            entity_id=decision_id,
            action="remove",
            old_value=json.dumps({"category": old["category"], "text": old["text"]}),
            new_value=None,
            origin="human",
            baseline_id=_current_baseline_id(conn),
            module_id=mid,
            component_id=cid,
        )
        db.delete_decisions_by_ids(conn, [decision_id])
        return {"ok": True}
    finally:
        db.close(conn)


# ---------------------------------------------------------------------------
# Node & Edge creation endpoints
# Doc: Natural_Language_Code/research_agent/info_map_editor.md
# ---------------------------------------------------------------------------

class ModuleCreate(BaseModel):
    name: str
    classification: str = "module"
    type: str | None = None
    technology: str | None = None


class ComponentCreate(BaseModel):
    module_id: int
    name: str
    purpose: str | None = None


class ModuleEdgeCreate(BaseModel):
    source_id: int
    target_id: int
    edge_type: str = "depends_on"
    label: str | None = None


class ComponentEdgeCreate(BaseModel):
    source_id: int
    target_id: int
    edge_type: str = "depends-on"
    label: str | None = None


@app.post("/api/modules")
async def create_module(body: ModuleCreate):
    """Create a new module node."""
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database not found")
    conn = db.connect(str(DB_PATH))
    try:
        new_id = db.add_module(
            conn,
            name=body.name,
            classification=body.classification,
            type=body.type,
            technology=body.technology,
            source_origin="in-repo",
            deployment_target=None,
        )
        db.add_change_record(
            conn,
            entity_type="module",
            entity_id=new_id,
            action="add",
            old_value=None,
            new_value=json.dumps({"name": body.name}),
            origin="human",
            baseline_id=_current_baseline_id(conn),
            module_id=new_id,
        )
        return {"id": new_id}
    finally:
        db.close(conn)


@app.post("/api/components")
async def create_component(body: ComponentCreate):
    """Create a new component node under a module."""
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database not found")
    conn = db.connect(str(DB_PATH))
    try:
        new_id = db.add_component(
            conn,
            module_id=body.module_id,
            name=body.name,
            purpose=body.purpose,
            confidence=None,
        )
        db.add_change_record(
            conn,
            entity_type="component",
            entity_id=new_id,
            action="add",
            old_value=None,
            new_value=json.dumps({"name": body.name}),
            origin="human",
            baseline_id=_current_baseline_id(conn),
            module_id=body.module_id,
            component_id=new_id,
        )
        return {"id": new_id}
    finally:
        db.close(conn)


@app.post("/api/module-edges")
async def create_module_edge(body: ModuleEdgeCreate):
    """Create an edge between two modules."""
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database not found")
    conn = db.connect(str(DB_PATH))
    try:
        metadata = json.dumps({"label": body.label}) if body.label else None
        db.add_module_edge(
            conn,
            source_id=body.source_id,
            target_id=body.target_id,
            edge_type=body.edge_type,
            weight=1.0,
            metadata=metadata,
        )
        return {"ok": True}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Edge already exists")
    finally:
        db.close(conn)


@app.post("/api/component-edges")
async def create_component_edge(body: ComponentEdgeCreate):
    """Create an edge between two components."""
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database not found")
    conn = db.connect(str(DB_PATH))
    try:
        metadata = json.dumps({"label": body.label}) if body.label else None
        db.add_component_edge(
            conn,
            source_id=body.source_id,
            target_id=body.target_id,
            edge_type=body.edge_type,
            weight=1.0,
            metadata=metadata,
        )
        return {"ok": True}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Edge already exists")
    finally:
        db.close(conn)


@app.delete("/api/modules/{module_id}")
async def delete_module_endpoint(module_id: int):
    """Remove a module (cascades to components, edges, decisions). Creates a change record."""
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database not found")
    conn = db.connect(str(DB_PATH))
    try:
        old = db.get_module(conn, module_id)
        if old is None:
            raise HTTPException(status_code=404, detail="Module not found")
        db.add_change_record(
            conn,
            entity_type="module",
            entity_id=module_id,
            action="remove",
            old_value=json.dumps({"name": old["name"]}),
            new_value=None,
            origin="human",
            baseline_id=_current_baseline_id(conn),
            module_id=module_id,
        )
        db.delete_module(conn, module_id)
        return {"ok": True}
    finally:
        db.close(conn)


@app.delete("/api/components/{component_id}")
async def delete_component_endpoint(component_id: int):
    """Remove a component (cascades to files, edges, decisions). Creates a change record."""
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database not found")
    conn = db.connect(str(DB_PATH))
    try:
        old = db.get_component(conn, component_id)
        if old is None:
            raise HTTPException(status_code=404, detail="Component not found")
        db.add_change_record(
            conn,
            entity_type="component",
            entity_id=component_id,
            action="remove",
            old_value=json.dumps({"name": old["name"]}),
            new_value=None,
            origin="human",
            baseline_id=_current_baseline_id(conn),
            module_id=old["module_id"],
            component_id=component_id,
        )
        db.delete_component(conn, component_id)
        return {"ok": True}
    finally:
        db.close(conn)


@app.get("/api/change-records")
async def get_change_records():
    """Return all change records since the current baseline, with entity context."""
    if not DB_PATH.exists():
        return {"baseline_id": None, "records": []}
    conn = db.connect(str(DB_PATH))
    try:
        baseline = db.get_current_baseline(conn)
        baseline_id = baseline["id"] if baseline else None
        raw = db.get_change_records(conn, since_baseline_id=baseline_id)

        records = []
        for rec in raw:
            r = dict(rec)
            for field in ("old_value", "new_value"):
                if r.get(field):
                    try:
                        r[field] = json.loads(r[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            # Context is stored directly on the row — no enrichment needed
            r["context"] = {
                "module_id": r.pop("module_id", None),
                "component_id": r.pop("component_id", None),
            }
            records.append(r)

        return {"baseline_id": baseline_id, "records": records}
    finally:
        db.close(conn)


# ---------------------------------------------------------------------------
# Ticket generation
# Doc: Natural_Language_Code/ticket_generation/info_ticket_generation.md
# ---------------------------------------------------------------------------

class TicketGenerateRequest(BaseModel):
    api_key: str
    model: str = "claude-haiku-4-5-20251001"


def _collapse_changes(records: list[dict]) -> list[dict]:
    """Net-delta collapse: reduce multiple edits to the same entity into one net change."""
    by_entity: dict[int, list[dict]] = {}
    for r in records:
        by_entity.setdefault(r["entity_id"], []).append(r)

    collapsed = []
    for entity_id, recs in by_entity.items():
        if len(recs) == 1:
            collapsed.append(recs[0])
            continue

        first = recs[0]
        last = recs[-1]
        first_action = first["action"]
        last_action = last["action"]

        # add + remove = cancel
        if first_action == "add" and last_action == "remove":
            continue
        # add + edit(s) = net add with final value
        if first_action == "add":
            net = dict(first)
            net["new_value"] = last["new_value"]
            collapsed.append(net)
        # edit(s) + remove = net remove with original old value
        elif last_action == "remove":
            net = dict(last)
            net["old_value"] = first["old_value"]
            collapsed.append(net)
        # multiple edits: first old → last new
        else:
            net = dict(first)
            net["new_value"] = last["new_value"]
            # If old == new after collapsing, it's a no-op — drop it
            if net["old_value"] == net["new_value"]:
                continue
            collapsed.append(net)

    return collapsed


def _build_ticket_prompt(collapsed: list[dict]) -> str:
    lines = [
        "You are converting architecture map changes into implementation tickets.",
        "Each ticket must be self-contained — include enough context that a developer can act on it without external references.",
        "",
        "Changes since last baseline:",
        "",
    ]
    for i, rec in enumerate(collapsed, 1):
        ctx = rec.get("context") or {}
        component = ctx.get("component_name") or "unknown component"
        module = ctx.get("module_name") or "unknown module"
        action = rec["action"].upper()
        lines.append(f"{i}. {action} decision on component \"{component}\" (module: {module})")
        if rec.get("old_value"):
            ov = rec["old_value"]
            if isinstance(ov, dict):
                lines.append(f"   Was: [{ov.get('category', '')}] {ov.get('text', '')}")
        if rec.get("new_value"):
            nv = rec["new_value"]
            if isinstance(nv, dict):
                lines.append(f"   Now: [{nv.get('category', '')}] {nv.get('text', '')}")
        lines.append("")

    lines += [
        "For each change that requires code implementation, produce a ticket.",
        "If the code already matches the change (the map was simply documenting existing reality), set is_map_correction: true.",
        "",
        "Return a JSON array only, no prose:",
        '[{"title":"...","description":"...","acceptance_criteria":"...","affected_files":[],"change_record_ids":[],"is_map_correction":false}]',
    ]
    return "\n".join(lines)


@app.post("/api/tickets/generate")
async def generate_tickets(body: TicketGenerateRequest):
    """Read change records since last baseline, call LLM, write tickets, advance baseline."""
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database not found")

    import litellm

    conn = db.connect(str(DB_PATH))
    try:
        baseline = db.get_current_baseline(conn)
        baseline_id = baseline["id"] if baseline else None
        raw_records = db.get_change_records(conn, since_baseline_id=baseline_id)

        if not raw_records:
            return {"tickets": [], "map_corrections": 0, "message": "No changes since last baseline"}

        # Parse JSON values; context is already on the row
        records_with_context = []
        for rec in raw_records:
            r = dict(rec)
            for field in ("old_value", "new_value"):
                if r.get(field):
                    try:
                        r[field] = json.loads(r[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            r["context"] = {
                "module_id": r.get("module_id"),
                "component_id": r.get("component_id"),
            }
            records_with_context.append(r)

        collapsed = _collapse_changes(records_with_context)
        if not collapsed:
            db.create_baseline(conn)
            return {"tickets": [], "map_corrections": 0, "message": "All changes cancelled each other out"}

        prompt = _build_ticket_prompt(collapsed)

        try:
            response = litellm.completion(
                model=body.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                api_key=body.api_key,
            )
        except litellm.AuthenticationError:
            raise HTTPException(
                status_code=401,
                detail="API authentication failed. Your API key may be invalid or expired.",
            )
        except (litellm.RateLimitError, litellm.APIError) as e:
            msg = str(e).lower()
            credit_keywords = ["insufficient", "quota", "billing", "credits", "budget", "exceeded", "payment", "balance", "plan limit", "spending limit"]
            if any(kw in msg for kw in credit_keywords):
                raise HTTPException(
                    status_code=402,
                    detail="You have run out of API credits. Please add credits to your account and try again.",
                )
            raise HTTPException(status_code=502, detail=f"LLM API error: {e}")

        raw_text = response.choices[0].message.content.strip()

        # Extract JSON array from response (handle markdown code fences)
        if "```" in raw_text:
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        ticket_list = json.loads(raw_text)

        saved_tickets = []
        map_corrections = 0
        run_id = db.start_pipeline_run(conn, "ticket_generation")

        for t in ticket_list:
            if t.get("is_map_correction"):
                map_corrections += 1
                continue

            # Coerce fields that the LLM may return as lists instead of strings
            def _to_str(v) -> str:
                if isinstance(v, list):
                    return "\n".join(str(i) for i in v)
                return str(v) if v else ""

            ticket_id = db.add_ticket(
                conn,
                title=_to_str(t.get("title")),
                description=_to_str(t.get("description")),
                acceptance_criteria=_to_str(t.get("acceptance_criteria")),
                run_id=run_id,
            )
            affected_files = t.get("affected_files") or []
            if isinstance(affected_files, str):
                affected_files = [affected_files]
            if affected_files:
                db.add_ticket_files(conn, ticket_id, affected_files)
            cr_ids = [r["id"] for r in raw_records if r["id"] in (t.get("change_record_ids") or [])]
            if cr_ids:
                db.link_ticket_change_records(conn, ticket_id, cr_ids)
            saved_tickets.append({
                "id": ticket_id,
                "title": _to_str(t.get("title")),
                "description": _to_str(t.get("description")),
                "acceptance_criteria": _to_str(t.get("acceptance_criteria")),
                "affected_files": affected_files,
            })

        db.complete_pipeline_run(conn, run_id, "completed")
        db.create_baseline(conn)

        return {"tickets": saved_tickets, "map_corrections": map_corrections}
    finally:
        db.close(conn)


@app.get("/api/tickets")
async def list_tickets():
    """Return all saved tickets with their files."""
    if not DB_PATH.exists():
        return {"tickets": []}
    conn = db.connect(str(DB_PATH))
    try:
        tickets = db.get_tickets(conn)
        for t in tickets:
            files = conn.execute(
                "SELECT path FROM ticket_files WHERE ticket_id = ?", (t["id"],)
            ).fetchall()
            t["files"] = [r["path"] for r in files]
        return {"tickets": tickets}
    finally:
        db.close(conn)


# ---------------------------------------------------------------------------
# Streaming run endpoint (SSE)
# ---------------------------------------------------------------------------

class StreamRunRequest(BaseModel):
    api_key: str
    provider: str = "anthropic"
    model: str | None = None
    step: str = "part1"  # "part1", "part2", "part3", "edges", "revalidation"
    repo_path: str | None = None


@app.post("/api/run/stream")
async def run_stream(req: StreamRunRequest):
    """Stream subprocess output as Server-Sent Events."""
    env_var = PROVIDER_ENV_MAP.get(req.provider)
    if not env_var:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'text': f'Unknown provider: {req.provider}'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    model = req.model or f"{req.provider}/claude-sonnet-4-20250514"
    if req.provider == "anthropic" and not req.model:
        model = "anthropic/claude-sonnet-4-20250514"
    elif req.provider == "openai" and not req.model:
        model = "openai/gpt-4o"

    if req.step == "part1":
        env = {**os.environ, env_var: req.api_key}
        source_dir = req.repo_path or str(PROJECT_DIR)

        async def part1_stream():
            # Validate source directory exists (macOS Docker silently fails otherwise)
            if not Path(source_dir).is_dir():
                yield f"data: {json.dumps({'type': 'error', 'text': f'Source directory not found: {source_dir}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                return

            OUTPUT_DIR.mkdir(exist_ok=True)

            # ----------------------------------------------------------------
            # Step 1A: Run SCIP indexer in parallel with opencode (modules)
            # ----------------------------------------------------------------
            modules_prompt = modules_system_prompt() + "\n\n---\n\n" + modules_variables_prompt(
                project_dir=source_dir,
                output_dir=str(OUTPUT_DIR),
            )

            # Use shell=True on Windows to handle long prompts via stdin piping
            # This avoids Windows command line length limits
            opencode_exe = _get_opencode_executable()
            opencode_cmd = [opencode_exe, "run", "--agent", "build", "-m", model]

            try:
                opencode_proc = await asyncio.create_subprocess_exec(
                    *opencode_cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(OUTPUT_DIR),
                    env=env,
                )
                # Write prompt to stdin and close it
                opencode_proc.stdin.write(modules_prompt.encode("utf-8"))
                await opencode_proc.stdin.drain()
                opencode_proc.stdin.close()
                await opencode_proc.stdin.wait_closed()
            except FileNotFoundError:
                yield f"data: {json.dumps({'type': 'error', 'text': 'opencode CLI not found. Install with: npm i -g opencode-ai@latest'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                return

            scip_cmd, scip_desc = _get_scip_cmd(source_dir)
            yield f"data: {json.dumps({'type': 'stdout', 'text': f'[SCIP] Starting indexer ({scip_desc})...'})}\n\n"
            yield f"data: {json.dumps({'type': 'stdout', 'text': '[Part 1] Identifying modules...'})}\n\n"

            try:
                scip_proc = await asyncio.create_subprocess_exec(
                    *scip_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env={**os.environ},
                )
            except FileNotFoundError as exc:
                yield f"data: {json.dumps({'type': 'stdout', 'text': f'[SCIP] Warning: indexer not found ({exc}). Part 2 will run it.'})}\n\n"
                scip_proc = None

            # Drain output from opencode + SCIP in parallel
            queue: asyncio.Queue = asyncio.Queue()
            _SENTINEL = object()

            async def drain(stream, prefix: str):
                async for line in stream:
                    text = line.decode("utf-8", errors="replace").rstrip("\n")
                    if text:
                        await queue.put(("stdout", f"{prefix} {text}"))
                await queue.put(_SENTINEL)

            drain_tasks = [
                asyncio.create_task(drain(opencode_proc.stdout, "[Part 1]")),
                asyncio.create_task(drain(opencode_proc.stderr, "[Part 1]")),
            ]
            if scip_proc is not None:
                drain_tasks.append(asyncio.create_task(drain(scip_proc.stdout, "[SCIP]")))

            expected = len(drain_tasks)
            received = 0
            while received < expected:
                item = await queue.get()
                if item is _SENTINEL:
                    received += 1
                else:
                    event_type, text = item
                    yield f"data: {json.dumps({'type': event_type, 'text': text})}\n\n"

            try:
                opencode_rc = await asyncio.wait_for(opencode_proc.wait(), timeout=60)
            except asyncio.TimeoutError:
                opencode_proc.terminate()
                opencode_rc = -1
                yield f"data: {json.dumps({'type': 'stderr', 'text': '[Part 1] opencode did not exit after output drained; terminating.'})}\n\n"

            if scip_proc is not None:
                scip_rc = await scip_proc.wait()
                if scip_rc != 0 and _is_docker_scip_cmd(scip_cmd):
                    # Docker volume mount may have failed — retry
                    for attempt in range(2, SCIP_DOCKER_MAX_RETRIES + 1):
                        yield f"data: {json.dumps({'type': 'stdout', 'text': f'[SCIP] Docker mount may have failed (exit {scip_rc}). Retrying ({attempt}/{SCIP_DOCKER_MAX_RETRIES})...'})}\n\n"
                        await asyncio.sleep(SCIP_DOCKER_RETRY_DELAY)
                        retry_proc = await asyncio.create_subprocess_exec(
                            *scip_cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.STDOUT,
                            env={**os.environ},
                        )
                        async for line in retry_proc.stdout:
                            text = line.decode("utf-8", errors="replace").rstrip("\n")
                            if text:
                                yield f"data: {json.dumps({'type': 'stdout', 'text': f'[SCIP] {text}'})}\n\n"
                        scip_rc = await retry_proc.wait()
                        if scip_rc == 0:
                            break

                if scip_rc != 0:
                    yield f"data: {json.dumps({'type': 'stdout', 'text': f'[SCIP] Warning: indexer exited with code {scip_rc}. Part 2 will retry.'})}\n\n"
                else:
                    scip_files = sorted(OUTPUT_DIR.glob("*.scip"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if scip_files:
                        yield f"data: {json.dumps({'type': 'stdout', 'text': f'[SCIP] Index ready: {scip_files[0].name}'})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'stdout', 'text': '[SCIP] Warning: indexer succeeded but no .scip file found. Part 2 will retry.'})}\n\n"

            if opencode_rc != 0:
                yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                return

            # ----------------------------------------------------------------
            # Step 1B: Ingest modules JSON → DB, build id_map
            # ----------------------------------------------------------------
            modules_path = OUTPUT_DIR / MODULES_FILENAME
            # Diagnostic: list any JSON files present after modules step
            json_present = [f.name for f in OUTPUT_DIR.glob("*.json")]
            yield f"data: {json.dumps({'type': 'stdout', 'text': f'[Part 1] JSON files in output/: {json_present}'})}\n\n"

            if not modules_path.exists():
                yield f"data: {json.dumps({'type': 'stderr', 'text': f'[Part 1] {MODULES_FILENAME} not found. Module step may have failed or used a different filename.'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                return

            conn = db.connect(str(DB_PATH))
            db.init_schema(conn)
            yield f"data: {json.dumps({'type': 'stdout', 'text': '[DB] Clearing old data...'})}\n\n"
            db.clear_modules(conn)
            run_id = db.start_pipeline_run(conn, "opencode_l2_classification")
            id_map: dict = {}
            try:
                id_map = ingest_l2_modules(conn, str(modules_path), run_id)
                yield f"data: {json.dumps({'type': 'stdout', 'text': f'[DB] Ingested {len(id_map)} module(s) into database.'})}\n\n"
            except Exception as e:
                db.complete_pipeline_run(conn, run_id, "failed")
                db.close(conn)
                yield f"data: {json.dumps({'type': 'stderr', 'text': f'[DB] Module ingestion failed: {e}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                return

            # ----------------------------------------------------------------
            # Step 2A: Run opencode (edges) — reads modules JSON, outputs edges JSON
            # ----------------------------------------------------------------
            yield f"data: {json.dumps({'type': 'stdout', 'text': '[Part 1] Identifying relationships between modules...'})}\n\n"

            edges_prompt = edges_system_prompt() + "\n\n---\n\n" + edges_variables_prompt(
                output_dir=str(OUTPUT_DIR),
            )

            edges_cmd = [_get_opencode_executable(), "run", "--agent", "build", "-m", model]

            try:
                edges_proc = await asyncio.create_subprocess_exec(
                    *edges_cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(OUTPUT_DIR),
                    env=env,
                )
                # Write prompt to stdin and close it
                edges_proc.stdin.write(edges_prompt.encode("utf-8"))
                await edges_proc.stdin.drain()
                edges_proc.stdin.close()
                await edges_proc.stdin.wait_closed()
            except FileNotFoundError:
                yield f"data: {json.dumps({'type': 'error', 'text': 'opencode CLI not found.'})}\n\n"
                db.complete_pipeline_run(conn, run_id, "failed")
                db.close(conn)
                yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                return

            # Drain stdout + stderr concurrently to avoid pipe buffer deadlock
            edges_queue: asyncio.Queue = asyncio.Queue()
            _EDGES_SENTINEL = object()

            async def drain_edges(stream, prefix: str):
                async for line in stream:
                    text = line.decode("utf-8", errors="replace").rstrip("\n")
                    if text:
                        await edges_queue.put(text)
                await edges_queue.put(_EDGES_SENTINEL)

            asyncio.create_task(drain_edges(edges_proc.stdout, "[Part 1]"))
            asyncio.create_task(drain_edges(edges_proc.stderr, "[Part 1]"))

            edges_received = 0
            while edges_received < 2:
                item = await edges_queue.get()
                if item is _EDGES_SENTINEL:
                    edges_received += 1
                else:
                    yield f"data: {json.dumps({'type': 'stdout', 'text': f'[Part 1] {item}'})}\n\n"

            try:
                edges_rc = await asyncio.wait_for(edges_proc.wait(), timeout=60)
            except asyncio.TimeoutError:
                edges_proc.terminate()
                edges_rc = -1
                yield f"data: {json.dumps({'type': 'stderr', 'text': '[Part 1] Edges step timed out; terminating.'})}\n\n"

            if edges_rc != 0:
                yield f"data: {json.dumps({'type': 'stderr', 'text': f'[Part 1] Edges step exited with code {edges_rc}.'})}\n\n"

            # ----------------------------------------------------------------
            # Step 2B: Ingest edges JSON → DB
            # ----------------------------------------------------------------
            edges_path = OUTPUT_DIR / EDGES_FILENAME
            # Diagnostic: list JSON files after edges step
            json_present2 = [f.name for f in OUTPUT_DIR.glob("*.json")]
            yield f"data: {json.dumps({'type': 'stdout', 'text': f'[Part 1] JSON files in output/ after edges step: {json_present2}'})}\n\n"

            if edges_path.exists() and id_map:
                try:
                    edge_count, skipped = ingest_l2_edges(conn, str(edges_path), id_map, run_id)
                    yield f"data: {json.dumps({'type': 'stdout', 'text': f'[DB] Ingested {edge_count} edge(s) from {EDGES_FILENAME}.'})}\n\n"
                    if skipped:
                        yield f"data: {json.dumps({'type': 'stderr', 'text': f'[DB] Skipped {len(skipped)} edge(s) with unresolved IDs: {skipped[:5]}'})}\n\n"
                        yield f"data: {json.dumps({'type': 'stderr', 'text': f'[DB] Known module IDs: {list(id_map.keys())}'})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'stderr', 'text': f'[DB] Edge ingestion failed: {e}'})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'stdout', 'text': f'[DB] {EDGES_FILENAME} not found (edges_rc={edges_rc}) — skipping edge ingestion.'})}\n\n"

            db.complete_pipeline_run(conn, run_id, "completed")
            db.close(conn)

            yield f"data: {json.dumps({'type': 'done', 'success': edges_rc == 0})}\n\n"

        return StreamingResponse(part1_stream(), media_type="text/event-stream")

    elif req.step == "part2":
        # Part 2: SCIP indexing (or reuse) + Component Discovery
        async def part2_stream():
            try:
                if not DB_PATH.exists():
                    yield f"data: {json.dumps({'type': 'error', 'text': 'No database found. Run Part 1 first.'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                    return

                source_dir = req.repo_path or str(PROJECT_DIR)
                if not Path(source_dir).is_dir():
                    yield f"data: {json.dumps({'type': 'error', 'text': f'Source directory not found: {source_dir}'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                    return

                OUTPUT_DIR.mkdir(exist_ok=True)

                # ---- Step 1: Use pre-built SCIP index or run indexer ----
                scip_files = sorted(OUTPUT_DIR.glob("*.scip"), key=lambda p: p.stat().st_mtime, reverse=True)
                if scip_files:
                    scip_path = [str(f) for f in scip_files]
                    names = ", ".join(f.name for f in scip_files)
                    yield f"data: {json.dumps({'type': 'stdout', 'text': f'[SCIP] Using pre-built index from Part 1: {names}'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'stdout', 'text': f'[SCIP] Indexing codebase: {source_dir}'})}\n\n"

                    scip_cmd, scip_desc = _get_scip_cmd(source_dir)
                    yield f"data: {json.dumps({'type': 'stdout', 'text': f'[SCIP] Using {scip_desc}'})}\n\n"

                    use_docker_retry = _is_docker_scip_cmd(scip_cmd)
                    scip_rc = None

                    for attempt in range(1, SCIP_DOCKER_MAX_RETRIES + 1 if use_docker_retry else 2):
                        if attempt > 1:
                            yield f"data: {json.dumps({'type': 'stdout', 'text': f'[SCIP] Docker mount may have failed. Retrying ({attempt}/{SCIP_DOCKER_MAX_RETRIES})...'})}\n\n"
                            await asyncio.sleep(SCIP_DOCKER_RETRY_DELAY)

                        scip_proc = await asyncio.create_subprocess_exec(
                            *scip_cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.STDOUT,
                            env={**os.environ},
                        )

                        async for line in scip_proc.stdout:
                            text = line.decode("utf-8", errors="replace").rstrip("\n")
                            if text:
                                yield f"data: {json.dumps({'type': 'stdout', 'text': f'[SCIP] {text}'})}\n\n"

                        scip_rc = await scip_proc.wait()
                        if scip_rc == 0:
                            break

                    if scip_rc != 0:
                        yield f"data: {json.dumps({'type': 'error', 'text': f'[SCIP] Indexer failed with exit code {scip_rc}'})}\n\n"
                        yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                        return

                    scip_files = sorted(OUTPUT_DIR.glob("*.scip"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if not scip_files:
                        yield f"data: {json.dumps({'type': 'error', 'text': '[SCIP] No .scip file produced. Check indexer output above.'})}\n\n"
                        yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                        return

                    scip_path = [str(f) for f in scip_files]
                    names = ", ".join(f.name for f in scip_files)
                    yield f"data: {json.dumps({'type': 'stdout', 'text': f'[SCIP] Index ready: {names}'})}\n\n"

                # ---- Step 2: Component Discovery ----
                yield f"data: {json.dumps({'type': 'stdout', 'text': '[Part 2] Starting component discovery...'})}\n\n"

                from component_discovery.pipeline import discover_all_components
                from component_discovery.llm_client import LLMClient

                client = LLMClient(model=model, api_key=req.api_key)

                log_q: _queue.Queue = _queue.Queue()

                def _log(msg: str) -> None:
                    log_q.put(msg)

                def _run_pipeline():
                    conn = db.connect(str(DB_PATH))
                    try:
                        return discover_all_components(conn, scip_path, source_dir, client, _log)
                    finally:
                        conn.close()

                try:
                    task = asyncio.create_task(
                        asyncio.to_thread(_run_pipeline)
                    )

                    # Drain log queue in real time while the pipeline thread runs
                    while not task.done():
                        try:
                            msg = log_q.get_nowait()
                            yield f"data: {json.dumps({'type': 'stdout', 'text': msg})}\n\n"
                        except _queue.Empty:
                            await asyncio.sleep(0.05)

                    # Drain any remaining messages posted before task.done() was seen
                    while not log_q.empty():
                        msg = log_q.get_nowait()
                        yield f"data: {json.dumps({'type': 'stdout', 'text': msg})}\n\n"

                    # Re-raise pipeline exceptions
                    await task

                    yield f"data: {json.dumps({'type': 'done', 'success': True})}\n\n"
                except Exception as e:
                    from component_discovery.llm_client import InsufficientCreditsError
                    if isinstance(e, InsufficientCreditsError):
                        yield f"data: {json.dumps({'type': 'error', 'text': f'[API Credits] {e}'})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'error', 'text': f'[Part 2] Pipeline failed: {e}'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"

            except Exception as e:
                from component_discovery.llm_client import InsufficientCreditsError
                if isinstance(e, InsufficientCreditsError):
                    yield f"data: {json.dumps({'type': 'error', 'text': f'[API Credits] {e}'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'error', 'text': f'[Part 2] Error: {e}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"

        return StreamingResponse(part2_stream(), media_type="text/event-stream")

    elif req.step == "part3":
        async def part3_stream():
            try:
                if not DB_PATH.exists():
                    yield f"data: {json.dumps({'type': 'error', 'text': 'No database found. Run Part 1 first.'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                    return

                source_dir = req.repo_path or str(PROJECT_DIR)
                if not Path(source_dir).is_dir():
                    yield f"data: {json.dumps({'type': 'error', 'text': f'Source directory not found: {source_dir}'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                    return

                yield f"data: {json.dumps({'type': 'stdout', 'text': '[Part 3] Starting map descriptions pipeline...'})}\n\n"

                from map_descriptions.pipeline import run_descriptions_pipeline

                await asyncio.to_thread(
                    run_descriptions_pipeline,
                    str(DB_PATH), source_dir, model, req.api_key,
                )

                yield f"data: {json.dumps({'type': 'stdout', 'text': '[Part 3] Map descriptions complete.'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'success': True})}\n\n"

            except Exception as e:
                from map_descriptions.llm_client import InsufficientCreditsError
                if isinstance(e, InsufficientCreditsError):
                    yield f"data: {json.dumps({'type': 'error', 'text': f'[API Credits] {e}'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'error', 'text': f'[Part 3] Pipeline failed: {e}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"

        return StreamingResponse(part3_stream(), media_type="text/event-stream")
    elif req.step == "edges":
        # Re-aggregate component edges from SCIP without re-running LLM.
        # Reads existing components + files from DB, parses all SCIP files,
        # and writes (or rewrites) component_edges for every qualifying module.
        async def edges_stream():
            try:
                if not DB_PATH.exists():
                    yield f"data: {json.dumps({'type': 'error', 'text': 'No database found. Run Part 1 + 2 first.'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                    return

                scip_files = sorted(OUTPUT_DIR.glob("*.scip"), key=lambda p: p.stat().st_mtime, reverse=True)
                if not scip_files:
                    yield f"data: {json.dumps({'type': 'error', 'text': 'No SCIP files found in output/. Run Part 2 first.'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                    return

                scip_paths = [str(f) for f in scip_files]
                names = ", ".join(f.name for f in scip_files)
                yield f"data: {json.dumps({'type': 'stdout', 'text': f'[Edges] SCIP files: {names}'})}\n\n"

                from component_discovery.scip_filter import parse_scip_for_module
                from component_discovery.edge_aggregator import aggregate_component_edges
                from component_discovery.edge_labeler import label_component_edges
                from component_discovery.llm_client import LLMClient

                edge_client = LLMClient(model=model, api_key=req.api_key)

                log_q: _queue.Queue = _queue.Queue()

                def _log(msg: str) -> None:
                    log_q.put(msg)

                def _run_edge_aggregation():
                    conn = db.connect(str(DB_PATH))
                    try:
                        modules = db.get_modules(conn)
                        total_edges = 0
                        processed = 0
                        skipped = 0

                        for module in modules:
                            if module["classification"] == "supporting-asset":
                                skipped += 1
                                continue
                            if module.get("source_origin") != "in-repo":
                                skipped += 1
                                continue

                            dirs = db.get_module_directories(conn, module["id"])
                            if not dirs:
                                skipped += 1
                                continue

                            components = db.get_components(conn, module["id"])
                            if len(components) < 2:
                                _log(f"[Edges] Skipping '{module['name']}': fewer than 2 components")
                                skipped += 1
                                continue

                            # Attach source files to each component
                            for comp in components:
                                comp["files"] = db.get_component_files(conn, comp["id"], exclude_test=True)

                            parsed = parse_scip_for_module(scip_paths, dirs)
                            _log(f"[Edges] '{module['name']}': {len(parsed['files'])} files, "
                                 f"{len(parsed['call_edges'])} call edges, "
                                 f"{len(parsed['import_edges'])} import edges")

                            # Aggregate: one edge per component pair
                            l3_edges = aggregate_component_edges(
                                components,
                                parsed["call_edges"],
                                parsed["import_edges"],
                                parsed["inheritance_edges"],
                            )

                            # LLM label in-memory before DB write
                            if l3_edges:
                                l3_edges = label_component_edges(l3_edges, components, edge_client, _log)

                            # Replace old edges for this module
                            conn.execute("""
                                DELETE FROM component_edges
                                WHERE source_id IN (SELECT id FROM components WHERE module_id = ?)
                                   OR target_id IN (SELECT id FROM components WHERE module_id = ?)
                            """, (module["id"], module["id"]))

                            name_to_id = {comp["name"]: comp["id"] for comp in components}
                            written = 0
                            for edge in l3_edges:
                                src = name_to_id.get(edge["source"])
                                tgt = name_to_id.get(edge["target"])
                                if src and tgt:
                                    metadata = json.dumps(edge.get("metadata", {}))
                                    db.add_component_edge(
                                        conn, src, tgt,
                                        edge["edge_type"], edge["weight"],
                                        metadata,
                                    )
                                    written += 1

                            conn.commit()
                            total_edges += written
                            processed += 1
                            _log(f"[Edges] '{module['name']}': {written} edge(s) written")

                        _log(f"[Edges] Done: {processed} module(s) processed, "
                             f"{skipped} skipped, {total_edges} edge(s) written")
                    finally:
                        conn.close()

                try:
                    task = asyncio.create_task(asyncio.to_thread(_run_edge_aggregation))

                    while not task.done():
                        try:
                            msg = log_q.get_nowait()
                            yield f"data: {json.dumps({'type': 'stdout', 'text': msg})}\n\n"
                        except _queue.Empty:
                            await asyncio.sleep(0.05)

                    while not log_q.empty():
                        msg = log_q.get_nowait()
                        yield f"data: {json.dumps({'type': 'stdout', 'text': msg})}\n\n"

                    await task
                    yield f"data: {json.dumps({'type': 'done', 'success': True})}\n\n"
                except Exception as e:
                    from component_discovery.llm_client import InsufficientCreditsError
                    if isinstance(e, InsufficientCreditsError):
                        yield f"data: {json.dumps({'type': 'error', 'text': f'[API Credits] {e}'})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'error', 'text': f'[Edges] Failed: {e}'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"

            except Exception as e:
                from component_discovery.llm_client import InsufficientCreditsError
                if isinstance(e, InsufficientCreditsError):
                    yield f"data: {json.dumps({'type': 'error', 'text': f'[API Credits] {e}'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'error', 'text': f'[Edges] Error: {e}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"

        return StreamingResponse(edges_stream(), media_type="text/event-stream")

    elif req.step == "revalidation":
        async def revalidation_stream():
            try:
                if not DB_PATH.exists():
                    yield f"data: {json.dumps({'type': 'error', 'text': 'No database found. Run Part 3 first.'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                    return

                source_dir = req.repo_path or str(PROJECT_DIR)
                if not Path(source_dir).is_dir():
                    yield f"data: {json.dumps({'type': 'error', 'text': f'Source directory not found: {source_dir}'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"
                    return

                yield f"data: {json.dumps({'type': 'stdout', 'text': '[Re-validate] Starting decision re-validation...'})}\n\n"

                from revalidation.pipeline import run_revalidation_pipeline

                await asyncio.to_thread(
                    run_revalidation_pipeline,
                    str(DB_PATH), source_dir, model, req.api_key,
                )

                yield f"data: {json.dumps({'type': 'stdout', 'text': '[Re-validate] Re-validation complete.'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'success': True})}\n\n"

            except Exception as e:
                from map_descriptions.llm_client import InsufficientCreditsError
                if isinstance(e, InsufficientCreditsError):
                    yield f"data: {json.dumps({'type': 'error', 'text': f'[API Credits] {e}'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'error', 'text': f'[Re-validate] Pipeline failed: {e}'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'success': False})}\n\n"

        return StreamingResponse(revalidation_stream(), media_type="text/event-stream")

    else:
        async def bad_step_stream():
            yield f"data: {json.dumps({'type': 'error', 'text': f'Unknown step: {req.step}'})}\n\n"
        return StreamingResponse(bad_step_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Map Versions (Snapshots)
# ---------------------------------------------------------------------------

@app.get("/api/versions")
async def list_versions():
    """Return all map versions, newest first."""
    if not DB_PATH.exists():
        return {"versions": []}
    conn = db.connect(str(DB_PATH))
    try:
        db.init_schema(conn)
        return {"versions": db.get_map_versions(conn)}
    finally:
        db.close(conn)


@app.get("/api/versions/{version_id}")
async def get_version(version_id: int):
    """Return a single version with its full decision snapshot."""
    if not DB_PATH.exists():
        return JSONResponse({"error": "No database found"}, status_code=404)
    conn = db.connect(str(DB_PATH))
    try:
        db.init_schema(conn)
        version = db.get_map_version(conn, version_id)
        if not version:
            return JSONResponse({"error": "Version not found"}, status_code=404)
        decisions = db.get_version_decisions(conn, version_id)
        return {"version": version, "decisions": decisions}
    finally:
        db.close(conn)


@app.post("/api/versions")
async def create_version():
    """Create a manual snapshot of the current map state."""
    if not DB_PATH.exists():
        return JSONResponse({"error": "No database found"}, status_code=404)
    conn = db.connect(str(DB_PATH))
    try:
        db.init_schema(conn)
        version_id = db.create_map_version(conn, trigger="manual")
        version = db.get_map_version(conn, version_id)
        return {"id": version_id, "version_number": version["version_number"]}
    finally:
        db.close(conn)


@app.get("/api/versions/{version_a_id}/compare/{version_b_id}")
async def compare_versions(version_a_id: int, version_b_id: int):
    """Diff two versions. Returns added/removed/changed decisions."""
    if not DB_PATH.exists():
        return JSONResponse({"error": "No database found"}, status_code=404)
    conn = db.connect(str(DB_PATH))
    try:
        db.init_schema(conn)
        return db.compare_versions(conn, version_a_id, version_b_id)
    finally:
        db.close(conn)


# ---------------------------------------------------------------------------
# Validation Runs (Re-validation)
# ---------------------------------------------------------------------------

@app.get("/api/validation-runs")
async def list_validation_runs():
    """Return all validation runs, newest first."""
    if not DB_PATH.exists():
        return {"runs": []}
    conn = db.connect(str(DB_PATH))
    try:
        db.init_schema(conn)
        return {"runs": db.get_validation_runs(conn)}
    finally:
        db.close(conn)


@app.get("/api/validation-runs/{run_id}")
async def get_validation_run_detail(run_id: int):
    """Return a validation run with all per-decision outcomes."""
    if not DB_PATH.exists():
        return JSONResponse({"error": "No database found"}, status_code=404)
    conn = db.connect(str(DB_PATH))
    try:
        db.init_schema(conn)
        run = db.get_validation_run(conn, run_id)
        if not run:
            return JSONResponse({"error": "Validation run not found"}, status_code=404)
        validations = db.get_decision_validations(conn, run_id)
        return {"run": run, "validations": validations}
    finally:
        db.close(conn)


@app.get("/api/validation/summary")
async def get_validation_summary():
    """Return latest re-validation results for frontend purple highlighting."""
    if not DB_PATH.exists():
        return {
            "validation_run_id": None,
            "affected_module_ids": [],
            "affected_component_ids": [],
            "decision_validations": [],
            "new_file_paths": [],
        }
    conn = db.connect(str(DB_PATH))
    try:
        db.init_schema(conn)
        return db.get_validation_summary(conn)
    finally:
        db.close(conn)


# ---------------------------------------------------------------------------
# Chat (MCP-based)
# ---------------------------------------------------------------------------

from chat import (
    get_or_create_session,
    delete_session,
    run_chat_turn,
    confirm_changes as do_confirm_changes,
    shutdown_mcp,
)


class ChatRequest(BaseModel):
    message: str
    mode: str = "ask"  # "ask" | "edit"
    session_id: str | None = None
    api_key: str
    provider: str = "anthropic"
    model: str | None = None


class ConfirmRequest(BaseModel):
    session_id: str
    change_ids: list[str]


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    """Stream a chat response as SSE events."""
    session = get_or_create_session(req.session_id)

    async def event_stream():
        try:
            async for event in run_chat_turn(
                message=req.message,
                mode=req.mode,
                session=session,
                db_path=str(DB_PATH),
                api_key=req.api_key,
                provider=req.provider,
                model=req.model,
            ):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            error_msg = str(e) or f"{type(e).__name__}: {repr(e)}"
            yield f"data: {json.dumps({'type': 'error', 'text': error_msg})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/chat/confirm")
async def confirm_changes_endpoint(req: ConfirmRequest):
    """Apply previously proposed changes."""
    results = await do_confirm_changes(
        session_id=req.session_id,
        change_ids=req.change_ids,
        db_path=str(DB_PATH),
    )
    return {"results": results}


@app.delete("/api/chat/session/{session_id}")
async def clear_chat_session(session_id: str):
    """Clear conversation history for a session."""
    deleted = delete_session(session_id)
    return {"ok": deleted}


@app.on_event("shutdown")
async def on_shutdown():
    await shutdown_mcp()
