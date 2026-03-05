# Doc: Natural_Language_Code/chat/info_chat.md

"""Legend MCP Server — exposes architecture graph data as MCP tools over stdio.

Usage:
    python mcp_server.py /path/to/legend.db
"""

import json
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

import db

DB_PATH: str = ""

server = Server("legend-mcp")


def _conn(readonly: bool = False):
    conn = db.connect(DB_PATH)
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    return conn


# ---------------------------------------------------------------------------
# Markdown formatting helpers (matches llmContext.ts export format)
# ---------------------------------------------------------------------------

def _render_decisions_md(decisions: list[dict], heading_prefix: str) -> str:
    """Group decisions by category and render as markdown."""
    if not decisions:
        return ""
    grouped: dict[str, list[dict]] = {}
    for d in decisions:
        cat = d.get("category") or "General"
        grouped.setdefault(cat, []).append(d)

    lines: list[str] = []
    for category, items in grouped.items():
        lines.append(f"{heading_prefix} {category}")
        for d in items:
            lines.append(f"- {d['text']} *(source: {d.get('source', 'unknown')})*")
            detail = d.get("detail")
            if detail:
                for detail_line in detail.split("\n"):
                    lines.append(f"  > {detail_line}")
            lines.append("")
        lines.append("")
    return "\n".join(lines)


def _render_component_md(comp: dict, files: list[dict], decisions: list[dict]) -> str:
    """Render a single component as markdown (matches llmContext.ts renderComponent)."""
    lines: list[str] = []
    lines.append(f"### {comp.get('name', 'Unknown')}")
    lines.append("")
    purpose = comp.get("purpose") or "No purpose specified"
    lines.append(f"**Purpose:** {purpose}")
    lines.append("")

    if files:
        lines.append("**Files:**")
        for f in files:
            suffix = " *(test)*" if f.get("is_test") else ""
            lines.append(f"- `{f['path']}`{suffix}")
        lines.append("")

    if decisions:
        lines.append("**Decisions:**")
        lines.append("")
        lines.append(_render_decisions_md(decisions, "####"))

    return "\n".join(lines)


def _get_component_files_with_test(conn, component_id: int) -> list[dict]:
    """Get component files with is_test flag from DB."""
    rows = conn.execute(
        "SELECT path, is_test FROM component_files WHERE component_id = ?",
        (component_id,),
    ).fetchall()
    return [{"path": r["path"], "is_test": bool(r["is_test"])} for r in rows]


# ---------------------------------------------------------------------------
# Tool catalogue
# ---------------------------------------------------------------------------

READ_TOOLS = [
    Tool(
        name="get_map_overview",
        description=(
            "Get a structural overview of the architecture map. Returns all modules with "
            "their directories, component/decision counts, and module dependency edges. "
            "Use this FIRST to understand application boundaries and overall structure "
            "before drilling into specific modules."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_module",
        description=(
            "Get a single module's metadata, directories, and module-level decisions. "
            "Does NOT include components — use get_module_components for that."
        ),
        inputSchema={
            "type": "object",
            "properties": {"module_id": {"type": "integer", "description": "Module ID"}},
            "required": ["module_id"],
        },
    ),
    Tool(
        name="get_module_components",
        description=(
            "Get all components in a module as formatted text. Each component includes "
            "its purpose, file paths, and technical decisions grouped by category."
        ),
        inputSchema={
            "type": "object",
            "properties": {"module_id": {"type": "integer", "description": "Module ID"}},
            "required": ["module_id"],
        },
    ),
    Tool(
        name="get_component",
        description=(
            "Get a single component as formatted text with purpose, file paths, "
            "and technical decisions grouped by category."
        ),
        inputSchema={
            "type": "object",
            "properties": {"component_id": {"type": "integer", "description": "Component ID"}},
            "required": ["component_id"],
        },
    ),
    Tool(
        name="get_decisions",
        description="Get technical decisions, optionally filtered by module or component.",
        inputSchema={
            "type": "object",
            "properties": {
                "module_id": {"type": "integer", "description": "Filter by module ID"},
                "component_id": {"type": "integer", "description": "Filter by component ID"},
            },
            "required": [],
        },
    ),
    Tool(
        name="get_module_edges",
        description="Get module-level dependency edges. Optionally filter by source module.",
        inputSchema={
            "type": "object",
            "properties": {
                "source_id": {"type": "integer", "description": "Filter by source module ID"},
            },
            "required": [],
        },
    ),
    Tool(
        name="get_component_edges",
        description="Get component-level dependency edges. Optionally filter by source component.",
        inputSchema={
            "type": "object",
            "properties": {
                "source_id": {"type": "integer", "description": "Filter by source component ID"},
            },
            "required": [],
        },
    ),
    Tool(
        name="search_entities",
        description="Search modules, components, and decisions by text. Searches names, decision text, and decision detail.",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search text"}},
            "required": ["query"],
        },
    ),
    Tool(
        name="get_change_records",
        description="Get pending change records since the last baseline.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
]

WRITE_TOOLS = [
    Tool(
        name="add_module",
        description="Create a new module in the architecture map.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Module name"},
                "classification": {"type": "string", "description": "module, shared-library, or supporting-asset", "default": "module"},
                "type": {"type": "string", "description": "Module type (e.g. service, library)"},
                "technology": {"type": "string", "description": "Primary technology"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="add_component",
        description="Create a new component within a module.",
        inputSchema={
            "type": "object",
            "properties": {
                "module_id": {"type": "integer", "description": "Parent module ID"},
                "name": {"type": "string", "description": "Component name"},
                "purpose": {"type": "string", "description": "Component purpose"},
            },
            "required": ["module_id", "name"],
        },
    ),
    Tool(
        name="add_decision",
        description="Add a technical decision to a module or component.",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Decision text (concise one-sentence summary)"},
                "category": {"type": "string", "description": "Category: api_contracts, patterns, libraries, boundaries, error_handling, data_flow, deployment, cross_cutting"},
                "detail": {"type": "string", "description": "Optional deeper context for the decision"},
                "module_id": {"type": "integer", "description": "Attach to this module (mutually exclusive with component_id)"},
                "component_id": {"type": "integer", "description": "Attach to this component (mutually exclusive with module_id)"},
            },
            "required": ["text", "category"],
        },
    ),
    Tool(
        name="update_decision",
        description="Edit an existing decision's text or category.",
        inputSchema={
            "type": "object",
            "properties": {
                "decision_id": {"type": "integer", "description": "Decision ID"},
                "text": {"type": "string", "description": "New text"},
                "category": {"type": "string", "description": "New category"},
                "detail": {"type": "string", "description": "New detail context"},
            },
            "required": ["decision_id"],
        },
    ),
    Tool(
        name="delete_decision",
        description="Remove a decision from the architecture map.",
        inputSchema={
            "type": "object",
            "properties": {
                "decision_id": {"type": "integer", "description": "Decision ID to delete"},
            },
            "required": ["decision_id"],
        },
    ),
    Tool(
        name="add_module_edge",
        description="Create a dependency edge between two modules.",
        inputSchema={
            "type": "object",
            "properties": {
                "source_id": {"type": "integer", "description": "Source module ID"},
                "target_id": {"type": "integer", "description": "Target module ID"},
                "edge_type": {"type": "string", "description": "depends_on, uses_data_store, or communicates_via"},
                "label": {"type": "string", "description": "Optional edge label"},
            },
            "required": ["source_id", "target_id", "edge_type"],
        },
    ),
    Tool(
        name="add_component_edge",
        description="Create a dependency edge between two components.",
        inputSchema={
            "type": "object",
            "properties": {
                "source_id": {"type": "integer", "description": "Source component ID"},
                "target_id": {"type": "integer", "description": "Target component ID"},
                "edge_type": {"type": "string", "description": "depends-on, call, import, or inheritance"},
                "label": {"type": "string", "description": "Optional edge label"},
            },
            "required": ["source_id", "target_id", "edge_type"],
        },
    ),
    Tool(
        name="delete_module",
        description="Delete a module and all its components, decisions, and edges (cascading).",
        inputSchema={
            "type": "object",
            "properties": {
                "module_id": {"type": "integer", "description": "Module ID to delete"},
            },
            "required": ["module_id"],
        },
    ),
    Tool(
        name="delete_component",
        description="Delete a component and its files, decisions, and edges (cascading).",
        inputSchema={
            "type": "object",
            "properties": {
                "component_id": {"type": "integer", "description": "Component ID to delete"},
            },
            "required": ["component_id"],
        },
    ),
]

WRITE_TOOL_NAMES = {t.name for t in WRITE_TOOLS}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _get_map_overview() -> dict:
    conn = _conn(readonly=True)
    try:
        modules_raw = db.get_modules(conn)
        modules_out = []
        for mod in modules_raw:
            mid = mod["id"]
            dirs = db.get_module_directories(conn, mid)
            comp_count = conn.execute(
                "SELECT COUNT(*) AS c FROM components WHERE module_id = ?", (mid,)
            ).fetchone()["c"]
            dec_count = conn.execute(
                "SELECT COUNT(*) AS c FROM decisions WHERE module_id = ?", (mid,)
            ).fetchone()["c"]
            modules_out.append({
                "id": mid,
                "name": mod["name"],
                "classification": mod.get("classification"),
                "technology": mod.get("technology"),
                "type": mod.get("type"),
                "directories": dirs,
                "component_count": comp_count,
                "decision_count": dec_count,
            })

        edges_raw = db.get_module_edges(conn)
        mod_names = {m["id"]: m["name"] for m in modules_raw}
        edges_summary = [
            {
                "source": mod_names.get(e["source_id"], f"?{e['source_id']}"),
                "target": mod_names.get(e["target_id"], f"?{e['target_id']}"),
                "edge_type": e.get("edge_type"),
            }
            for e in edges_raw
        ]

        total_components = conn.execute("SELECT COUNT(*) AS c FROM components").fetchone()["c"]
        total_decisions = conn.execute("SELECT COUNT(*) AS c FROM decisions").fetchone()["c"]

        class_rows = conn.execute(
            "SELECT classification, COUNT(*) AS c FROM modules GROUP BY classification"
        ).fetchall()
        by_classification = {r["classification"]: r["c"] for r in class_rows}

        return {
            "modules": modules_out,
            "module_edges": edges_summary,
            "totals": {
                "modules": len(modules_out),
                "components": total_components,
                "decisions": total_decisions,
                "by_classification": by_classification,
            },
        }
    finally:
        db.close(conn)


def _get_module(module_id: int) -> dict:
    conn = _conn(readonly=True)
    try:
        mod = db.get_module(conn, module_id)
        if not mod:
            return {"error": f"Module {module_id} not found"}
        mod["directories"] = db.get_module_directories(conn, module_id)
        mod["decisions"] = db.get_decisions(conn, module_id=module_id)
        return mod
    finally:
        db.close(conn)


def _get_module_components(module_id: int) -> str:
    conn = _conn(readonly=True)
    try:
        mod = db.get_module(conn, module_id)
        if not mod:
            return json.dumps({"error": f"Module {module_id} not found"})

        components = db.get_components(conn, module_id=module_id)
        if not components:
            return f"# {mod['name']} — Components\n\nNo components found."

        lines = [f"# {mod['name']} — Components ({len(components)} total)", ""]
        for comp in components:
            files = _get_component_files_with_test(conn, comp["id"])
            decisions = db.get_decisions(conn, component_id=comp["id"])
            lines.append(_render_component_md(comp, files, decisions))
            lines.append("---")
            lines.append("")

        return "\n".join(lines)
    finally:
        db.close(conn)


def _get_component(component_id: int) -> str:
    conn = _conn(readonly=True)
    try:
        comp = db.get_component(conn, component_id)
        if not comp:
            return json.dumps({"error": f"Component {component_id} not found"})
        files = _get_component_files_with_test(conn, component_id)
        decisions = db.get_decisions(conn, component_id=component_id)
        return _render_component_md(comp, files, decisions)
    finally:
        db.close(conn)


def _get_decisions(module_id: int | None = None, component_id: int | None = None) -> list:
    conn = _conn(readonly=True)
    try:
        return db.get_decisions(conn, module_id=module_id, component_id=component_id)
    finally:
        db.close(conn)


def _get_module_edges(source_id: int | None = None) -> list:
    conn = _conn(readonly=True)
    try:
        return db.get_module_edges(conn, source_id=source_id)
    finally:
        db.close(conn)


def _get_component_edges(source_id: int | None = None) -> list:
    conn = _conn(readonly=True)
    try:
        return db.get_component_edges(conn, source_id=source_id)
    finally:
        db.close(conn)


def _search_entities(query: str) -> dict:
    conn = _conn(readonly=True)
    try:
        q = f"%{query}%"
        modules = conn.execute(
            "SELECT id, name, classification, type, technology FROM modules WHERE name LIKE ?",
            (q,),
        ).fetchall()
        components = conn.execute(
            "SELECT c.id, c.name, c.purpose, c.module_id, m.name AS module_name "
            "FROM components c JOIN modules m ON c.module_id = m.id "
            "WHERE c.name LIKE ?",
            (q,),
        ).fetchall()
        decisions = conn.execute(
            "SELECT d.id, d.category, d.text, d.detail, d.module_id, d.component_id, "
            "COALESCE(m.name, '') AS module_name, COALESCE(c.name, '') AS component_name "
            "FROM decisions d "
            "LEFT JOIN modules m ON d.module_id = m.id "
            "LEFT JOIN components c ON d.component_id = c.id "
            "WHERE d.text LIKE ? OR d.detail LIKE ?",
            (q, q),
        ).fetchall()
        return {
            "modules": [dict(r) for r in modules],
            "components": [dict(r) for r in components],
            "decisions": [dict(r) for r in decisions],
        }
    finally:
        db.close(conn)


def _get_change_records() -> list:
    conn = _conn(readonly=True)
    try:
        baseline = db.get_current_baseline(conn)
        baseline_id = baseline["id"] if baseline else None
        return db.get_change_records(conn, since_baseline_id=baseline_id)
    finally:
        db.close(conn)


# --- Write tools ---

def _add_module(name: str, classification: str = "module", type: str | None = None, technology: str | None = None) -> dict:
    conn = _conn()
    try:
        mid = db.add_module(conn, name=name, classification=classification, type=type, technology=technology, source_origin=None, deployment_target=None)
        db.add_change_record(conn, entity_type="module", entity_id=mid, action="add", old_value=None, new_value=json.dumps({"name": name, "classification": classification}), origin="chat", module_id=mid)
        return {"id": mid}
    finally:
        db.close(conn)


def _add_component(module_id: int, name: str, purpose: str | None = None) -> dict:
    conn = _conn()
    try:
        cid = db.add_component(conn, module_id=module_id, name=name, purpose=purpose, confidence=None)
        db.add_change_record(conn, entity_type="component", entity_id=cid, action="add", old_value=None, new_value=json.dumps({"name": name, "module_id": module_id}), origin="chat", module_id=module_id, component_id=cid)
        return {"id": cid}
    finally:
        db.close(conn)


def _add_decision(text: str, category: str, module_id: int | None = None, component_id: int | None = None, detail: str | None = None) -> dict:
    conn = _conn()
    try:
        did = db.add_decision(conn, category=category, text=text, module_id=module_id, component_id=component_id, source="chat", detail=detail)
        db.add_change_record(conn, entity_type="decision", entity_id=did, action="add", old_value=None, new_value=json.dumps({"category": category, "text": text}), origin="chat", module_id=module_id, component_id=component_id)
        return {"id": did}
    finally:
        db.close(conn)


def _update_decision(decision_id: int, text: str | None = None, category: str | None = None, detail: str | None = None) -> dict:
    conn = _conn()
    try:
        old = db.get_decision(conn, decision_id)
        if not old:
            return {"error": f"Decision {decision_id} not found"}
        updates = {}
        if text is not None:
            updates["text"] = text
        if category is not None:
            updates["category"] = category
        if detail is not None:
            updates["detail"] = detail
        if updates:
            updates["source"] = "chat"
            db.update_decision(conn, decision_id, **updates)
            db.add_change_record(conn, entity_type="decision", entity_id=decision_id, action="edit", old_value=json.dumps({"category": old["category"], "text": old["text"]}), new_value=json.dumps({"category": category or old["category"], "text": text or old["text"]}), origin="chat", module_id=old.get("module_id"), component_id=old.get("component_id"))
        return {"ok": True}
    finally:
        db.close(conn)


def _delete_decision(decision_id: int) -> dict:
    conn = _conn()
    try:
        old = db.get_decision(conn, decision_id)
        if not old:
            return {"error": f"Decision {decision_id} not found"}
        db.add_change_record(conn, entity_type="decision", entity_id=decision_id, action="remove", old_value=json.dumps({"category": old["category"], "text": old["text"]}), new_value=None, origin="chat", module_id=old.get("module_id"), component_id=old.get("component_id"))
        db.delete_decisions_by_ids(conn, [decision_id])
        return {"ok": True}
    finally:
        db.close(conn)


def _add_module_edge(source_id: int, target_id: int, edge_type: str, label: str | None = None) -> dict:
    conn = _conn()
    try:
        metadata = json.dumps({"label": label}) if label else None
        db.add_module_edge(conn, source_id=source_id, target_id=target_id, edge_type=edge_type, metadata=metadata)
        return {"ok": True}
    finally:
        db.close(conn)


def _add_component_edge(source_id: int, target_id: int, edge_type: str, label: str | None = None) -> dict:
    conn = _conn()
    try:
        metadata = json.dumps({"label": label}) if label else None
        db.add_component_edge(conn, source_id=source_id, target_id=target_id, edge_type=edge_type, metadata=metadata)
        return {"ok": True}
    finally:
        db.close(conn)


def _delete_module(module_id: int) -> dict:
    conn = _conn()
    try:
        old = db.get_module(conn, module_id)
        if not old:
            return {"error": f"Module {module_id} not found"}
        db.add_change_record(conn, entity_type="module", entity_id=module_id, action="remove", old_value=json.dumps({"name": old["name"]}), new_value=None, origin="chat", module_id=module_id)
        db.delete_module(conn, module_id)
        return {"ok": True}
    finally:
        db.close(conn)


def _delete_component(component_id: int) -> dict:
    conn = _conn()
    try:
        old = db.get_component(conn, component_id)
        if not old:
            return {"error": f"Component {component_id} not found"}
        db.add_change_record(conn, entity_type="component", entity_id=component_id, action="remove", old_value=json.dumps({"name": old["name"]}), new_value=None, origin="chat", module_id=old.get("module_id"), component_id=component_id)
        db.delete_component(conn, component_id)
        return {"ok": True}
    finally:
        db.close(conn)


# ---------------------------------------------------------------------------
# MCP handlers
# ---------------------------------------------------------------------------

TOOL_DISPATCH = {
    "get_map_overview": lambda args: _get_map_overview(),
    "get_module": lambda args: _get_module(args["module_id"]),
    "get_module_components": lambda args: _get_module_components(args["module_id"]),
    "get_component": lambda args: _get_component(args["component_id"]),
    "get_decisions": lambda args: _get_decisions(args.get("module_id"), args.get("component_id")),
    "get_module_edges": lambda args: _get_module_edges(args.get("source_id")),
    "get_component_edges": lambda args: _get_component_edges(args.get("source_id")),
    "search_entities": lambda args: _search_entities(args["query"]),
    "get_change_records": lambda args: _get_change_records(),
    "add_module": lambda args: _add_module(args["name"], args.get("classification", "module"), args.get("type"), args.get("technology")),
    "add_component": lambda args: _add_component(args["module_id"], args["name"], args.get("purpose")),
    "add_decision": lambda args: _add_decision(args["text"], args["category"], args.get("module_id"), args.get("component_id"), args.get("detail")),
    "update_decision": lambda args: _update_decision(args["decision_id"], args.get("text"), args.get("category"), args.get("detail")),
    "delete_decision": lambda args: _delete_decision(args["decision_id"]),
    "add_module_edge": lambda args: _add_module_edge(args["source_id"], args["target_id"], args["edge_type"], args.get("label")),
    "add_component_edge": lambda args: _add_component_edge(args["source_id"], args["target_id"], args["edge_type"], args.get("label")),
    "delete_module": lambda args: _delete_module(args["module_id"]),
    "delete_component": lambda args: _delete_component(args["component_id"]),
}


@server.list_tools()
async def list_tools():
    return READ_TOOLS + WRITE_TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    handler = TOOL_DISPATCH.get(name)
    if not handler:
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
    try:
        result = handler(arguments)
        if isinstance(result, str):
            return [TextContent(type="text", text=result)]
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    global DB_PATH
    if len(sys.argv) < 2:
        print("Usage: python mcp_server.py <db_path>", file=sys.stderr)
        sys.exit(1)
    DB_PATH = sys.argv[1]

    # Ensure DB exists and schema is initialized
    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    db.close(conn)

    async with stdio_server() as (read_stream, write_stream):
        init_options = server.create_initialization_options()
        await server.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
