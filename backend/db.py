# Doc: Natural_Language_Code/database/info_database.md

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Transaction management
# ---------------------------------------------------------------------------
# id()s of connections currently inside a managed transaction block. Helpers
# check membership before their per-call commit, so an atomic block groups many
# helper calls into a single commit/rollback. Behavior outside a transaction is
# identical to before (each helper commits itself). We key by id() because
# sqlite3.Connection objects are not weak-referenceable / hashable-for-WeakSet
# on all builds; the id is only tracked while the connection is alive inside an
# active block, so there is no id-reuse hazard.
_txn_conns: set[int] = set()
_txn_lock = threading.Lock()


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Atomic block. Re-entrant (inner blocks join the outer one).

    Helpers called inside skip their per-call commit; the block commits on
    success and rolls back on any exception.
    """
    key = id(conn)
    with _txn_lock:
        reentrant = key in _txn_conns
        if not reentrant:
            _txn_conns.add(key)
    if reentrant:
        # Inner block: just run, the outermost block owns commit/rollback.
        yield conn
        return
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        with _txn_lock:
            _txn_conns.discard(key)


def _commit(conn: sqlite3.Connection) -> None:
    """Commit unless conn is inside a managed transaction block."""
    with _txn_lock:
        inside = id(conn) in _txn_conns
    if not inside:
        conn.commit()


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def connect(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite file, enable WAL mode and foreign keys."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist, then run lightweight migrations."""
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns/constraints that may be missing from older databases."""
    for col in ("module_id", "component_id"):
        try:
            conn.execute(f"ALTER TABLE change_records ADD COLUMN {col} INTEGER")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    # Migrate component_edges UNIQUE constraint to include edge_type
    # (old: UNIQUE(source_id, target_id), new: UNIQUE(source_id, target_id, edge_type))
    try:
        # Check if migration is needed by inspecting the existing constraint
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='component_edges'"
        ).fetchone()
        if row and "UNIQUE(source_id, target_id)" in row[0] and "edge_type" not in row[0].split("UNIQUE")[1]:
            conn.execute("""
                CREATE TABLE component_edges_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
                    target_id INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
                    edge_type TEXT,
                    weight REAL DEFAULT 1.0,
                    metadata TEXT,
                    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
                    UNIQUE(source_id, target_id, edge_type)
                )
            """)
            conn.execute("INSERT INTO component_edges_new SELECT * FROM component_edges")
            conn.execute("DROP TABLE component_edges")
            conn.execute("ALTER TABLE component_edges_new RENAME TO component_edges")
            conn.commit()
    except sqlite3.OperationalError:
        pass  # already migrated or component_edges doesn't exist yet

    # Migrate decisions table: relax the module/component CHECK and add the
    # orphaned_module_name / orphaned_component_name columns used for parking
    # human-authored decisions. Rebuild is required because SQLite cannot alter
    # a CHECK constraint in place. IDs are copied verbatim so ticket_decisions,
    # change_records, version_decisions and decision_validations references stay
    # valid.
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(decisions)").fetchall()]
        if cols and "orphaned_module_name" not in cols:
            conn.commit()  # close any open txn (PRAGMA foreign_keys is a no-op mid-txn)
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("""
                CREATE TABLE decisions_new (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_id       INTEGER REFERENCES modules(id) ON DELETE CASCADE,
                    component_id    INTEGER REFERENCES components(id) ON DELETE CASCADE,
                    category        TEXT NOT NULL,
                    text            TEXT NOT NULL,
                    detail          TEXT,
                    source          TEXT NOT NULL DEFAULT 'pipeline_generated',
                    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    orphaned_module_name    TEXT,
                    orphaned_component_name TEXT,
                    CHECK (NOT (module_id IS NOT NULL AND component_id IS NOT NULL))
                )
            """)
            conn.execute("""
                INSERT INTO decisions_new
                    (id, module_id, component_id, category, text, detail, source, pipeline_run_id, created_at)
                SELECT id, module_id, component_id, category, text, detail, source, pipeline_run_id, created_at
                FROM decisions
            """)
            conn.execute("DROP TABLE decisions")
            conn.execute("ALTER TABLE decisions_new RENAME TO decisions")
            conn.commit()
            conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.OperationalError:
        pass  # already migrated or decisions doesn't exist yet


def close(conn: sqlite3.Connection) -> None:
    """Close the connection."""
    conn.close()


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------

def start_pipeline_run(conn: sqlite3.Connection, step: str) -> int:
    """Insert a new run record, return its ID."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO pipeline_runs (step, started_at, status) VALUES (?, ?, 'running')",
        (step, now),
    )
    _commit(conn)
    return cur.lastrowid


def complete_pipeline_run(
    conn: sqlite3.Connection, run_id: int, status: str = "completed"
) -> None:
    """Set completed_at and status."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE pipeline_runs SET completed_at = ?, status = ? WHERE id = ?",
        (now, status, run_id),
    )
    _commit(conn)


# ---------------------------------------------------------------------------
# Modules (Part 1)
# ---------------------------------------------------------------------------

def add_module(
    conn: sqlite3.Connection,
    name: str,
    classification: str,
    type: str | None,
    technology: str | None,
    source_origin: str | None,
    deployment_target: str | None,
    run_id: int | None = None,
) -> int:
    """Insert a module and return its ID."""
    cur = conn.execute(
        """INSERT INTO modules
           (name, classification, type, technology, source_origin, deployment_target, pipeline_run_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, classification, type, technology, source_origin, deployment_target, run_id),
    )
    _commit(conn)
    return cur.lastrowid


def add_module_directories(
    conn: sqlite3.Connection, module_id: int, paths: list[str]
) -> None:
    """Insert directory-to-module mappings."""
    conn.executemany(
        "INSERT INTO module_directories (module_id, path) VALUES (?, ?)",
        [(module_id, p) for p in paths],
    )
    _commit(conn)


def get_module_directories(conn: sqlite3.Connection, module_id: int) -> list[str]:
    """Return directory paths for a module."""
    rows = conn.execute(
        "SELECT path FROM module_directories WHERE module_id = ?", (module_id,)
    ).fetchall()
    return [r["path"] for r in rows]


def get_modules(
    conn: sqlite3.Connection, classification: str | None = None
) -> list[dict]:
    """Return modules, optionally filtered by classification."""
    if classification:
        rows = conn.execute(
            "SELECT * FROM modules WHERE classification = ?", (classification,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM modules").fetchall()
    return [dict(r) for r in rows]


def get_module(conn: sqlite3.Connection, module_id: int) -> dict | None:
    """Return a single module by ID."""
    row = conn.execute("SELECT * FROM modules WHERE id = ?", (module_id,)).fetchone()
    return dict(row) if row else None


def clear_modules(conn: sqlite3.Connection) -> None:
    """Delete all modules (cascades to directories, components, etc.)."""
    conn.execute("DELETE FROM modules")
    _commit(conn)


# ---------------------------------------------------------------------------
# Components (Part 2)
# ---------------------------------------------------------------------------

def add_component(
    conn: sqlite3.Connection,
    module_id: int,
    name: str,
    purpose: str | None,
    confidence: float | None,
    run_id: int | None = None,
) -> int:
    """Insert a component and return its ID."""
    cur = conn.execute(
        """INSERT INTO components
           (module_id, name, purpose, confidence, pipeline_run_id)
           VALUES (?, ?, ?, ?, ?)""",
        (module_id, name, purpose, confidence, run_id),
    )
    _commit(conn)
    return cur.lastrowid


def add_component_files(
    conn: sqlite3.Connection,
    component_id: int,
    paths: list[str],
    is_test: list[bool],
) -> None:
    """Insert file-to-component mappings."""
    conn.executemany(
        "INSERT INTO component_files (component_id, path, is_test) VALUES (?, ?, ?)",
        [(component_id, p, int(t)) for p, t in zip(paths, is_test)],
    )
    _commit(conn)


def get_components(
    conn: sqlite3.Connection, module_id: int | None = None
) -> list[dict]:
    """Return components, optionally filtered by module."""
    if module_id is not None:
        rows = conn.execute(
            "SELECT * FROM components WHERE module_id = ?", (module_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM components").fetchall()
    return [dict(r) for r in rows]


def get_component(conn: sqlite3.Connection, component_id: int) -> dict | None:
    """Return a single component by ID."""
    row = conn.execute(
        "SELECT * FROM components WHERE id = ?", (component_id,)
    ).fetchone()
    return dict(row) if row else None


def get_component_files(
    conn: sqlite3.Connection, component_id: int, exclude_test: bool = False
) -> list[str]:
    """Return file paths for a component. When exclude_test=True, filters out test files."""
    if exclude_test:
        rows = conn.execute(
            "SELECT path FROM component_files WHERE component_id = ? AND is_test = 0",
            (component_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT path FROM component_files WHERE component_id = ?",
            (component_id,),
        ).fetchall()
    return [r["path"] for r in rows]


def get_all_component_file_paths(conn: sqlite3.Connection) -> set[str]:
    """Return all tracked file paths across all components."""
    rows = conn.execute("SELECT path FROM component_files").fetchall()
    return {r["path"] for r in rows}


# ---------------------------------------------------------------------------
# Module Edges (Part 1)
# ---------------------------------------------------------------------------

def add_module_edge(
    conn: sqlite3.Connection,
    source_id: int,
    target_id: int,
    edge_type: str | None,
    weight: float = 1.0,
    metadata: str | None = None,
    run_id: int | None = None,
) -> None:
    """Insert a module-to-module edge."""
    conn.execute(
        """INSERT INTO module_edges
           (source_id, target_id, edge_type, weight, metadata, pipeline_run_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (source_id, target_id, edge_type, weight, metadata, run_id),
    )
    _commit(conn)


def upsert_module_edge(
    conn: sqlite3.Connection,
    source_id: int,
    target_id: int,
    edge_type: str | None,
    weight: float = 1.0,
    metadata: str | None = None,
    run_id: int | None = None,
) -> None:
    """Insert a module edge, or update it in place if (source, target, edge_type)
    already exists. Used by reconciling ingest so re-runs don't collide on the
    UNIQUE(source_id, target_id, edge_type) constraint."""
    conn.execute(
        """INSERT INTO module_edges
           (source_id, target_id, edge_type, weight, metadata, pipeline_run_id)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_id, target_id, edge_type)
           DO UPDATE SET weight = excluded.weight,
                         metadata = excluded.metadata,
                         pipeline_run_id = excluded.pipeline_run_id""",
        (source_id, target_id, edge_type, weight, metadata, run_id),
    )
    _commit(conn)


def get_module_edges(
    conn: sqlite3.Connection, source_id: int | None = None
) -> list[dict]:
    """Return module edges, optionally filtered by source."""
    if source_id is not None:
        rows = conn.execute(
            "SELECT * FROM module_edges WHERE source_id = ?", (source_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM module_edges").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Component Edges (Part 2)
# ---------------------------------------------------------------------------

def add_component_edge(
    conn: sqlite3.Connection,
    source_id: int,
    target_id: int,
    edge_type: str | None,
    weight: float = 1.0,
    metadata: str | None = None,
    run_id: int | None = None,
) -> None:
    """Insert a component-to-component edge."""
    conn.execute(
        """INSERT INTO component_edges
           (source_id, target_id, edge_type, weight, metadata, pipeline_run_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (source_id, target_id, edge_type, weight, metadata, run_id),
    )
    _commit(conn)


def get_component_edges(
    conn: sqlite3.Connection, source_id: int | None = None
) -> list[dict]:
    """Return component edges, optionally filtered by source."""
    if source_id is not None:
        rows = conn.execute(
            "SELECT * FROM component_edges WHERE source_id = ?", (source_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM component_edges").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Decisions (Part 3)
# ---------------------------------------------------------------------------

def add_decision(
    conn: sqlite3.Connection,
    category: str,
    text: str,
    module_id: int | None = None,
    component_id: int | None = None,
    source: str = "pipeline_generated",
    run_id: int | None = None,
    detail: str | None = None,
) -> int:
    """Insert a decision (must have module_id XOR component_id). Return its ID."""
    cur = conn.execute(
        """INSERT INTO decisions
           (module_id, component_id, category, text, detail, source, pipeline_run_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (module_id, component_id, category, text, detail, source, run_id),
    )
    _commit(conn)
    return cur.lastrowid


def get_decisions(
    conn: sqlite3.Connection,
    module_id: int | None = None,
    component_id: int | None = None,
) -> list[dict]:
    """Return decisions filtered by module or component (or all)."""
    if module_id is not None:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE module_id = ?", (module_id,)
        ).fetchall()
    elif component_id is not None:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE component_id = ?", (component_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM decisions").fetchall()
    return [dict(r) for r in rows]


def get_decision(conn: sqlite3.Connection, decision_id: int) -> dict | None:
    """Return a single decision by ID, or None if not found."""
    row = conn.execute(
        "SELECT * FROM decisions WHERE id = ?", (decision_id,)
    ).fetchone()
    return dict(row) if row else None


def delete_decisions_by_ids(conn: sqlite3.Connection, ids: list[int]) -> None:
    """Delete specific decisions by primary key."""
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM decisions WHERE id IN ({placeholders})", ids)
    _commit(conn)


def clear_decisions(
    conn: sqlite3.Connection, source: str = "pipeline_generated"
) -> None:
    """Remove only decisions with the given source, preserving others."""
    conn.execute("DELETE FROM decisions WHERE source = ?", (source,))
    _commit(conn)


# ---------------------------------------------------------------------------
# Parking / re-attaching human decisions across pipeline re-runs
# ---------------------------------------------------------------------------
# When the pipeline reaps an entity it created, any human/chat-authored
# decisions on that entity would otherwise cascade away. Instead we "park" them:
# detach (module_id/component_id -> NULL) and remember the entity's name(s) so
# they can be re-attached automatically if an entity with that name reappears.
# "Human-authored" means source != 'pipeline_generated' (covers 'human' and the
# 'chat' source written by MCP chat tools).

def park_module_decisions(
    conn: sqlite3.Connection, module_id: int, module_name: str
) -> int:
    """Detach non-pipeline decisions of a module AND of all its components,
    before the module row is deleted. Records where each decision lived.
    Pipeline-generated decisions are left to cascade away. Returns count parked.
    """
    # Component decisions first — capture each component's name via subquery
    # BEFORE the components are deleted by the module cascade.
    cur_comp = conn.execute(
        """UPDATE decisions
           SET component_id = NULL,
               orphaned_module_name = ?,
               orphaned_component_name = (
                   SELECT name FROM components WHERE id = decisions.component_id
               )
           WHERE component_id IN (SELECT id FROM components WHERE module_id = ?)
             AND source != 'pipeline_generated'""",
        (module_name, module_id),
    )
    # Then the module's own decisions
    cur_mod = conn.execute(
        """UPDATE decisions
           SET module_id = NULL,
               orphaned_module_name = ?
           WHERE module_id = ?
             AND source != 'pipeline_generated'""",
        (module_name, module_id),
    )
    _commit(conn)
    return cur_comp.rowcount + cur_mod.rowcount


def park_component_decisions(
    conn: sqlite3.Connection, component_id: int, module_name: str, component_name: str
) -> int:
    """Detach non-pipeline decisions of a single component before it is deleted.
    Returns count parked."""
    cur = conn.execute(
        """UPDATE decisions
           SET component_id = NULL,
               orphaned_module_name = ?,
               orphaned_component_name = ?
           WHERE component_id = ?
             AND source != 'pipeline_generated'""",
        (module_name, component_name, component_id),
    )
    _commit(conn)
    return cur.rowcount


def reattach_orphaned_decisions(conn: sqlite3.Connection) -> int:
    """Re-attach parked decisions whose home entity exists again. Clears the
    orphan columns on re-attach. Returns count re-attached."""
    # Component decisions: match component name within the named module.
    cur_comp = conn.execute(
        """UPDATE decisions
           SET component_id = (
                   SELECT c.id FROM components c
                   JOIN modules m ON c.module_id = m.id
                   WHERE m.name = decisions.orphaned_module_name
                     AND c.name = decisions.orphaned_component_name
                   LIMIT 1
               ),
               orphaned_module_name = NULL,
               orphaned_component_name = NULL
           WHERE module_id IS NULL AND component_id IS NULL
             AND orphaned_component_name IS NOT NULL
             AND EXISTS (
                   SELECT 1 FROM components c
                   JOIN modules m ON c.module_id = m.id
                   WHERE m.name = decisions.orphaned_module_name
                     AND c.name = decisions.orphaned_component_name
               )"""
    )
    # Module decisions (no component name): match module name.
    cur_mod = conn.execute(
        """UPDATE decisions
           SET module_id = (
                   SELECT id FROM modules
                   WHERE name = decisions.orphaned_module_name
                   LIMIT 1
               ),
               orphaned_module_name = NULL,
               orphaned_component_name = NULL
           WHERE module_id IS NULL AND component_id IS NULL
             AND orphaned_component_name IS NULL
             AND orphaned_module_name IS NOT NULL
             AND EXISTS (
                   SELECT 1 FROM modules WHERE name = decisions.orphaned_module_name
               )"""
    )
    _commit(conn)
    return cur_comp.rowcount + cur_mod.rowcount


def get_orphaned_decisions(conn: sqlite3.Connection) -> list[dict]:
    """Return all parked (detached) decisions."""
    rows = conn.execute(
        "SELECT * FROM decisions WHERE module_id IS NULL AND component_id IS NULL"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Reconciliation (upsert pipeline output by stable name identity)
# ---------------------------------------------------------------------------
# reconcile_* upsert pipeline output against existing rows keyed by stable name
# identity (module.name UNIQUE; component (module_id, name) UNIQUE), so entity
# IDs survive re-runs and everything hanging off them (decisions, change_records,
# ticket_decisions) stays valid. Human-created entities (pipeline_run_id IS NULL)
# are never reaped — the pipeline only reaps what the pipeline created.
#
# change_records policy: reconciliation writes NO change_records. Matched
# entities keep their IDs so existing change_records/ticket_decisions stay valid.
# Known limitation: an LLM renaming a module reads as remove+add, so human
# decisions are parked (visible via the orphaned endpoint) and re-attach only if
# the old name returns. Only exact-name matching is done here — no fuzzy/rename.

def reconcile_modules(
    conn: sqlite3.Connection, incoming: list[dict], run_id: int | None
) -> dict[str, int]:
    """Upsert pipeline module output against existing modules by name.

    incoming entries: {name, classification, type, technology, source_origin,
    deployment_target, directories: list[str]}. Returns {module_name: module_id}.
    Call inside db.transaction().
    """
    existing = {row["name"]: row for row in get_modules(conn)}
    incoming_names = {m["name"] for m in incoming}
    id_by_name: dict[str, int] = {}

    for m in incoming:
        name = m["name"]
        directories = m.get("directories") or []
        if name in existing:
            module_id = existing[name]["id"]
            conn.execute(
                """UPDATE modules
                   SET classification = ?, type = ?, technology = ?,
                       source_origin = ?, deployment_target = ?, pipeline_run_id = ?
                   WHERE id = ?""",
                (
                    m.get("classification", "module"),
                    m.get("type"),
                    m.get("technology"),
                    m.get("source_origin"),
                    m.get("deployment_target"),
                    run_id,
                    module_id,
                ),
            )
            conn.execute("DELETE FROM module_directories WHERE module_id = ?", (module_id,))
            if directories:
                add_module_directories(conn, module_id, directories)
        else:
            module_id = add_module(
                conn,
                name=name,
                classification=m.get("classification", "module"),
                type=m.get("type"),
                technology=m.get("technology"),
                source_origin=m.get("source_origin"),
                deployment_target=m.get("deployment_target"),
                run_id=run_id,
            )
            if directories:
                add_module_directories(conn, module_id, directories)
        id_by_name[name] = module_id

    # Reap pipeline-created modules that are no longer present.
    for name, row in existing.items():
        if name in incoming_names:
            continue
        if row["pipeline_run_id"] is None:
            continue  # human/chat-created — leave untouched
        park_module_decisions(conn, row["id"], name)
        conn.execute("DELETE FROM modules WHERE id = ?", (row["id"],))

    reattach_orphaned_decisions(conn)
    return id_by_name


def reconcile_components(
    conn: sqlite3.Connection, module_id: int, incoming: list[dict], run_id: int | None
) -> dict[str, int]:
    """Upsert pipeline component output for one module by (module_id, name).

    incoming entries: {name, purpose, confidence, files: list[tuple[path, is_test]]}.
    Returns {component_name: component_id}. Call inside db.transaction().
    """
    module_row = get_module(conn, module_id)
    module_name = module_row["name"] if module_row else ""
    existing = {row["name"]: row for row in get_components(conn, module_id)}
    incoming_names = {c["name"] for c in incoming}
    name_to_id: dict[str, int] = {}

    for c in incoming:
        name = c["name"]
        files = c.get("files") or []
        if name in existing:
            component_id = existing[name]["id"]
            conn.execute(
                """UPDATE components
                   SET purpose = ?, confidence = ?, pipeline_run_id = ?
                   WHERE id = ?""",
                (c.get("purpose"), c.get("confidence"), run_id, component_id),
            )
            conn.execute("DELETE FROM component_files WHERE component_id = ?", (component_id,))
            if files:
                add_component_files(
                    conn, component_id, [p for p, _t in files], [bool(t) for _p, t in files]
                )
        else:
            component_id = add_component(
                conn, module_id, name, c.get("purpose"), c.get("confidence"), run_id
            )
            if files:
                add_component_files(
                    conn, component_id, [p for p, _t in files], [bool(t) for _p, t in files]
                )
        name_to_id[name] = component_id

    # Reap pipeline-created components no longer present.
    for name, row in existing.items():
        if name in incoming_names:
            continue
        if row["pipeline_run_id"] is None:
            continue  # human/chat-created — leave untouched
        park_component_decisions(conn, row["id"], module_name, name)
        conn.execute("DELETE FROM components WHERE id = ?", (row["id"],))

    reattach_orphaned_decisions(conn)
    return name_to_id


# ---------------------------------------------------------------------------
# Research Agent CRUD
# ---------------------------------------------------------------------------

def update_decision(conn: sqlite3.Connection, decision_id: int, **fields) -> None:
    """Update any decision field (category, text, source, etc.)."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [decision_id]
    conn.execute(f"UPDATE decisions SET {set_clause} WHERE id = ?", values)
    _commit(conn)


def delete_module(conn: sqlite3.Connection, module_id: int) -> None:
    """Delete module with cascade (directories, components, edges, decisions)."""
    conn.execute("DELETE FROM modules WHERE id = ?", (module_id,))
    _commit(conn)


def delete_component(conn: sqlite3.Connection, component_id: int) -> None:
    """Delete component with cascade (component_files, component_edges, decisions)."""
    conn.execute("DELETE FROM components WHERE id = ?", (component_id,))
    _commit(conn)


def move_component_files(
    conn: sqlite3.Connection,
    file_paths: list[str],
    from_component_id: int,
    to_component_id: int,
) -> None:
    """Reassign files between components."""
    if not file_paths:
        return
    placeholders = ",".join("?" for _ in file_paths)
    conn.execute(
        f"""UPDATE component_files
            SET component_id = ?
            WHERE component_id = ? AND path IN ({placeholders})""",
        [to_component_id, from_component_id] + file_paths,
    )
    _commit(conn)


# ---------------------------------------------------------------------------
# Change Records (Research Agent)
# ---------------------------------------------------------------------------

def add_change_record(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    action: str,
    old_value: str | None,
    new_value: str | None,
    origin: str,
    baseline_id: int | None = None,
    module_id: int | None = None,
    component_id: int | None = None,
) -> int:
    """Create a change record and return its ID."""
    cur = conn.execute(
        """INSERT INTO change_records
           (entity_type, entity_id, action, old_value, new_value, origin, baseline_id, module_id, component_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (entity_type, entity_id, action, old_value, new_value, origin, baseline_id, module_id, component_id),
    )
    _commit(conn)
    return cur.lastrowid


def get_change_records(
    conn: sqlite3.Connection, since_baseline_id: int | None = None
) -> list[dict]:
    """Return change records since the given baseline (or all if None)."""
    if since_baseline_id is not None:
        rows = conn.execute(
            "SELECT * FROM change_records WHERE baseline_id = ?",
            (since_baseline_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM change_records").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Baselines (Ticket Generation)
# ---------------------------------------------------------------------------

def create_baseline(conn: sqlite3.Connection, run_id: int | None = None) -> int:
    """Create a baseline and return its ID."""
    cur = conn.execute(
        "INSERT INTO baselines (pipeline_run_id) VALUES (?)", (run_id,)
    )
    _commit(conn)
    return cur.lastrowid


def get_current_baseline(conn: sqlite3.Connection) -> dict | None:
    """Return the most recent baseline, or None if none exists."""
    row = conn.execute(
        "SELECT * FROM baselines ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Tickets (Ticket Generation)
# ---------------------------------------------------------------------------

def add_ticket(
    conn: sqlite3.Connection,
    title: str,
    description: str,
    acceptance_criteria: str | None,
    run_id: int | None = None,
) -> int:
    """Insert a ticket and return its ID."""
    cur = conn.execute(
        """INSERT INTO tickets
           (title, description, acceptance_criteria, pipeline_run_id)
           VALUES (?, ?, ?, ?)""",
        (title, description, acceptance_criteria, run_id),
    )
    _commit(conn)
    return cur.lastrowid


def add_ticket_files(
    conn: sqlite3.Connection, ticket_id: int, paths: list[str]
) -> None:
    """Insert file paths affected by a ticket."""
    conn.executemany(
        "INSERT INTO ticket_files (ticket_id, path) VALUES (?, ?)",
        [(ticket_id, p) for p in paths],
    )
    _commit(conn)


def add_ticket_decisions(
    conn: sqlite3.Connection, ticket_id: int, decision_ids: list[int]
) -> None:
    """Link ticket to its source decisions."""
    conn.executemany(
        "INSERT INTO ticket_decisions (ticket_id, decision_id) VALUES (?, ?)",
        [(ticket_id, d) for d in decision_ids],
    )
    _commit(conn)


def link_ticket_change_records(
    conn: sqlite3.Connection, ticket_id: int, change_record_ids: list[int]
) -> None:
    """Link ticket to its source change records."""
    conn.executemany(
        "INSERT INTO ticket_change_records (ticket_id, change_record_id) VALUES (?, ?)",
        [(ticket_id, cr) for cr in change_record_ids],
    )
    _commit(conn)


def get_tickets(
    conn: sqlite3.Connection, status: str | None = None
) -> list[dict]:
    """Return tickets, optionally filtered by status."""
    if status:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE status = ?", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tickets").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Map Versions (Snapshots)
# ---------------------------------------------------------------------------

def create_map_version(
    conn: sqlite3.Connection,
    trigger: str,
    run_id: int | None = None,
) -> int:
    """Snapshot all current decisions into a new map version. Return version ID."""
    # Next version number
    row = conn.execute(
        "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_ver FROM map_versions"
    ).fetchone()
    next_ver = row["next_ver"]

    cur = conn.execute(
        "INSERT INTO map_versions (version_number, trigger, pipeline_run_id) VALUES (?, ?, ?)",
        (next_ver, trigger, run_id),
    )
    version_id = cur.lastrowid

    # Copy all decisions with denormalized module/component names
    conn.execute(
        """INSERT INTO version_decisions
           (version_id, decision_id, module_id, component_id,
            module_name, component_name, category, text, source)
        SELECT ?, d.id, d.module_id, d.component_id,
               COALESCE(m.name, ''), COALESCE(c.name, ''),
               d.category, d.text, d.source
        FROM decisions d
        LEFT JOIN modules m ON d.module_id = m.id
        LEFT JOIN components c ON d.component_id = c.id""",
        (version_id,),
    )

    # Compute and store summary
    stats = conn.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(CASE WHEN module_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS module_decisions,
                  COALESCE(SUM(CASE WHEN component_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS component_decisions
           FROM version_decisions WHERE version_id = ?""",
        (version_id,),
    ).fetchone()
    mod_count = conn.execute("SELECT COUNT(*) AS c FROM modules").fetchone()["c"]
    comp_count = conn.execute("SELECT COUNT(*) AS c FROM components").fetchone()["c"]

    summary = json.dumps({
        "total_decisions": stats["total"],
        "module_decisions": stats["module_decisions"],
        "component_decisions": stats["component_decisions"],
        "modules": mod_count,
        "components": comp_count,
    })
    conn.execute("UPDATE map_versions SET summary = ? WHERE id = ?", (summary, version_id))
    _commit(conn)
    return version_id


def get_map_versions(conn: sqlite3.Connection) -> list[dict]:
    """Return all versions ordered by version_number descending."""
    rows = conn.execute(
        "SELECT * FROM map_versions ORDER BY version_number DESC"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("summary"):
            try:
                d["summary"] = json.loads(d["summary"])
            except (json.JSONDecodeError, TypeError):
                d["summary"] = None
        result.append(d)
    return result


def get_map_version(conn: sqlite3.Connection, version_id: int) -> dict | None:
    """Return a single version by ID with parsed summary."""
    row = conn.execute(
        "SELECT * FROM map_versions WHERE id = ?", (version_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("summary"):
        try:
            d["summary"] = json.loads(d["summary"])
        except (json.JSONDecodeError, TypeError):
            d["summary"] = None
    return d


def get_version_decisions(
    conn: sqlite3.Connection,
    version_id: int,
    module_id: int | None = None,
    component_id: int | None = None,
) -> list[dict]:
    """Return snapshotted decisions for a version, optionally filtered."""
    if module_id is not None:
        rows = conn.execute(
            "SELECT * FROM version_decisions WHERE version_id = ? AND module_id = ?",
            (version_id, module_id),
        ).fetchall()
    elif component_id is not None:
        rows = conn.execute(
            "SELECT * FROM version_decisions WHERE version_id = ? AND component_id = ?",
            (version_id, component_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM version_decisions WHERE version_id = ?", (version_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def compare_versions(
    conn: sqlite3.Connection,
    version_a_id: int,
    version_b_id: int,
) -> dict:
    """Diff two versions. Returns added/removed/changed decisions."""
    decisions_a = {
        r["decision_id"]: r
        for r in get_version_decisions(conn, version_a_id)
        if r["decision_id"] is not None
    }
    decisions_b = {
        r["decision_id"]: r
        for r in get_version_decisions(conn, version_b_id)
        if r["decision_id"] is not None
    }

    ids_a = set(decisions_a.keys())
    ids_b = set(decisions_b.keys())

    added = [decisions_b[did] for did in (ids_b - ids_a)]
    removed = [decisions_a[did] for did in (ids_a - ids_b)]

    changed = []
    unchanged = 0
    for did in ids_a & ids_b:
        a = decisions_a[did]
        b = decisions_b[did]
        if a["text"] != b["text"] or a["category"] != b["category"]:
            changed.append({
                "decision_id": did,
                "module_name": b.get("module_name"),
                "component_name": b.get("component_name"),
                "old": {"category": a["category"], "text": a["text"], "source": a["source"]},
                "new": {"category": b["category"], "text": b["text"], "source": b["source"]},
            })
        else:
            unchanged += 1

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged_count": unchanged,
        "version_a": get_map_version(conn, version_a_id),
        "version_b": get_map_version(conn, version_b_id),
    }


# ---------------------------------------------------------------------------
# Validation Runs (Re-validation Pipeline)
# ---------------------------------------------------------------------------

def start_validation_run(
    conn: sqlite3.Connection,
    before_version_id: int,
    model: str,
    run_id: int | None = None,
) -> int:
    """Create a validation run linked to the before-snapshot. Return its ID."""
    cur = conn.execute(
        """INSERT INTO validation_runs
           (pipeline_run_id, before_version_id, model)
           VALUES (?, ?, ?)""",
        (run_id, before_version_id, model),
    )
    _commit(conn)
    return cur.lastrowid


def complete_validation_run(
    conn: sqlite3.Connection,
    validation_run_id: int,
    after_version_id: int,
    status: str = "completed",
    summary: dict | None = None,
) -> None:
    """Set after_version_id, status, and summary on a validation run."""
    summary_json = json.dumps(summary) if summary else None
    conn.execute(
        """UPDATE validation_runs
           SET after_version_id = ?, status = ?, summary = ?
           WHERE id = ?""",
        (after_version_id, status, summary_json, validation_run_id),
    )
    _commit(conn)


def get_validation_runs(conn: sqlite3.Connection) -> list[dict]:
    """Return all validation runs, newest first, with parsed summary."""
    rows = conn.execute(
        "SELECT * FROM validation_runs ORDER BY created_at DESC"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("summary"):
            try:
                d["summary"] = json.loads(d["summary"])
            except (json.JSONDecodeError, TypeError):
                d["summary"] = None
        result.append(d)
    return result


def get_validation_run(conn: sqlite3.Connection, validation_run_id: int) -> dict | None:
    """Return a single validation run with parsed summary."""
    row = conn.execute(
        "SELECT * FROM validation_runs WHERE id = ?", (validation_run_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("summary"):
        try:
            d["summary"] = json.loads(d["summary"])
        except (json.JSONDecodeError, TypeError):
            d["summary"] = None
    return d


# ---------------------------------------------------------------------------
# Decision Validations (Re-validation Outcomes)
# ---------------------------------------------------------------------------

def add_decision_validation(
    conn: sqlite3.Connection,
    validation_run_id: int,
    decision_id: int | None,
    source: str,
    status: str,
    old_text: str | None = None,
    new_text: str | None = None,
    reason: str | None = None,
    category: str | None = None,
    module_name: str | None = None,
    component_name: str | None = None,
) -> int:
    """Record one decision's validation outcome. Return the row ID."""
    cur = conn.execute(
        """INSERT INTO decision_validations
           (validation_run_id, decision_id, source, status,
            old_text, new_text, reason, category, module_name, component_name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (validation_run_id, decision_id, source, status,
         old_text, new_text, reason, category, module_name, component_name),
    )
    _commit(conn)
    return cur.lastrowid


def get_decision_validations(
    conn: sqlite3.Connection,
    validation_run_id: int,
) -> list[dict]:
    """Return all per-decision outcomes for a validation run."""
    rows = conn.execute(
        "SELECT * FROM decision_validations WHERE validation_run_id = ?",
        (validation_run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_validation_summary(conn: sqlite3.Connection) -> dict:
    """Return latest re-validation results for frontend purple highlighting.

    Returns only non-confirmed/non-unchanged validations with affected node IDs.
    """
    latest_run = conn.execute(
        "SELECT * FROM validation_runs WHERE status = 'completed' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not latest_run:
        return {
            "validation_run_id": None,
            "affected_module_ids": [],
            "affected_component_ids": [],
            "decision_validations": [],
            "new_file_paths": [],
        }

    run = dict(latest_run)
    run_id = run["id"]

    # Get non-trivial validations (exclude confirmed and unchanged)
    rows = conn.execute(
        """SELECT dv.*, d.module_id, d.component_id
           FROM decision_validations dv
           LEFT JOIN decisions d ON dv.decision_id = d.id
           WHERE dv.validation_run_id = ?
             AND dv.status NOT IN ('confirmed', 'unchanged')""",
        (run_id,),
    ).fetchall()

    validations = [dict(r) for r in rows]
    module_ids = set()
    component_ids = set()
    for v in validations:
        if v.get("module_id"):
            module_ids.add(v["module_id"])
        if v.get("component_id"):
            component_ids.add(v["component_id"])

    # Extract new_file_paths from run summary JSON
    summary = json.loads(run["summary"]) if run.get("summary") else {}
    new_file_paths = summary.get("new_file_paths", [])

    return {
        "validation_run_id": run_id,
        "affected_module_ids": list(module_ids),
        "affected_component_ids": list(component_ids),
        "decision_validations": validations,
        "new_file_paths": new_file_paths,
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_full_map(conn: sqlite3.Connection) -> dict:
    """Nested dict with modules -> components -> decisions + edges for frontend."""
    modules = []
    for mod in conn.execute("SELECT * FROM modules").fetchall():
        mod_dict = dict(mod)
        mod_id = mod_dict["id"]

        # Module directories
        dirs = conn.execute(
            "SELECT path FROM module_directories WHERE module_id = ?", (mod_id,)
        ).fetchall()
        mod_dict["directories"] = [r["path"] for r in dirs]

        # Module-level decisions
        mod_decisions = conn.execute(
            "SELECT id, category, text, detail, source FROM decisions WHERE module_id = ?",
            (mod_id,),
        ).fetchall()
        mod_dict["decisions"] = [dict(r) for r in mod_decisions]

        # Components within this module
        components = []
        for comp in conn.execute(
            "SELECT * FROM components WHERE module_id = ?", (mod_id,)
        ).fetchall():
            comp_dict = dict(comp)
            comp_id = comp_dict["id"]

            # Component files
            files = conn.execute(
                "SELECT path, is_test FROM component_files WHERE component_id = ?",
                (comp_id,),
            ).fetchall()
            comp_dict["files"] = [
                {"path": r["path"], "is_test": bool(r["is_test"])} for r in files
            ]

            # Component-level decisions
            comp_decisions = conn.execute(
                "SELECT id, category, text, detail, source FROM decisions WHERE component_id = ?",
                (comp_id,),
            ).fetchall()
            comp_dict["decisions"] = [dict(r) for r in comp_decisions]

            components.append(comp_dict)

        mod_dict["components"] = components
        modules.append(mod_dict)

    # All edges (parse metadata JSON)
    module_edges = []
    for r in conn.execute(
        "SELECT source_id, target_id, edge_type, weight, metadata FROM module_edges"
    ).fetchall():
        edge_dict = dict(r)
        if edge_dict["metadata"]:
            import json
            try:
                edge_dict["metadata"] = json.loads(edge_dict["metadata"])
            except (json.JSONDecodeError, TypeError):
                edge_dict["metadata"] = {}
        else:
            edge_dict["metadata"] = {}
        module_edges.append(edge_dict)

    component_edges = []
    for r in conn.execute(
        "SELECT source_id, target_id, edge_type, weight, metadata FROM component_edges"
    ).fetchall():
        edge_dict = dict(r)
        if edge_dict["metadata"]:
            import json
            try:
                edge_dict["metadata"] = json.loads(edge_dict["metadata"])
            except (json.JSONDecodeError, TypeError):
                edge_dict["metadata"] = {}
        else:
            edge_dict["metadata"] = {}
        component_edges.append(edge_dict)

    return {
        "modules": modules,
        "module_edges": module_edges,
        "component_edges": component_edges,
    }


def import_full_map(conn: sqlite3.Connection, data: dict) -> dict:
    """Import a full map from exported JSON. Clears existing data first.

    Handles ID remapping: exported integer IDs are replaced with
    fresh autoincrement IDs from the target database.

    The entire import runs inside a single transaction: malformed input rolls
    back and the previous map survives.
    """
    with transaction(conn):
        return _import_full_map_inner(conn, data)


def _import_full_map_inner(conn: sqlite3.Connection, data: dict) -> dict:
    clear_modules(conn)

    module_id_map: dict[int, int] = {}
    component_id_map: dict[int, int] = {}
    decision_count = 0

    for mod in data.get("modules", []):
        old_mod_id = mod["id"]
        new_mod_id = add_module(
            conn,
            name=mod["name"],
            classification=mod.get("classification", "module"),
            type=mod.get("type"),
            technology=mod.get("technology"),
            source_origin=mod.get("source_origin"),
            deployment_target=mod.get("deployment_target"),
        )
        module_id_map[old_mod_id] = new_mod_id

        dirs = mod.get("directories", [])
        if dirs:
            add_module_directories(conn, new_mod_id, dirs)

        for dec in mod.get("decisions", []):
            add_decision(
                conn,
                category=dec["category"],
                text=dec["text"],
                module_id=new_mod_id,
                source=dec.get("source", "pipeline_generated"),
                detail=dec.get("detail"),
            )
            decision_count += 1

        for comp in mod.get("components", []):
            old_comp_id = comp["id"]
            new_comp_id = add_component(
                conn,
                module_id=new_mod_id,
                name=comp["name"],
                purpose=comp.get("purpose"),
                confidence=comp.get("confidence"),
            )
            component_id_map[old_comp_id] = new_comp_id

            files = comp.get("files", [])
            if files:
                add_component_files(
                    conn,
                    new_comp_id,
                    [f["path"] for f in files],
                    [f.get("is_test", False) for f in files],
                )

            for dec in comp.get("decisions", []):
                add_decision(
                    conn,
                    category=dec["category"],
                    text=dec["text"],
                    component_id=new_comp_id,
                    source=dec.get("source", "pipeline_generated"),
                    detail=dec.get("detail"),
                )
                decision_count += 1

    me_count = 0
    for edge in data.get("module_edges", []):
        src = module_id_map.get(edge["source_id"])
        tgt = module_id_map.get(edge["target_id"])
        if src and tgt:
            metadata = json.dumps(edge["metadata"]) if edge.get("metadata") else None
            add_module_edge(conn, src, tgt, edge.get("edge_type"), edge.get("weight", 1.0), metadata)
            me_count += 1

    ce_count = 0
    for edge in data.get("component_edges", []):
        src = component_id_map.get(edge["source_id"])
        tgt = component_id_map.get(edge["target_id"])
        if src and tgt:
            metadata = json.dumps(edge["metadata"]) if edge.get("metadata") else None
            add_component_edge(conn, src, tgt, edge.get("edge_type"), edge.get("weight", 1.0), metadata)
            ce_count += 1

    return {
        "modules": len(module_id_map),
        "components": len(component_id_map),
        "decisions": decision_count,
        "module_edges": me_count,
        "component_edges": ce_count,
    }


# ---------------------------------------------------------------------------
# Schema SQL (18 tables)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
-- Track pipeline execution history
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    step        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    completed_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    metadata    TEXT
);

-- L2 modules, shared libraries, and supporting assets (from Part 1)
CREATE TABLE IF NOT EXISTS modules (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    classification    TEXT NOT NULL DEFAULT 'module',
    type              TEXT,
    technology        TEXT,
    source_origin     TEXT,
    deployment_target TEXT,
    pipeline_run_id   INTEGER REFERENCES pipeline_runs(id),
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Directory-to-module mapping (join table)
CREATE TABLE IF NOT EXISTS module_directories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id   INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    UNIQUE(module_id, path)
);

-- L3 components within modules (from Part 2)
CREATE TABLE IF NOT EXISTS components (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id       INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    purpose         TEXT,
    confidence      REAL,
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(module_id, name)
);

-- File-to-component mapping (join table)
CREATE TABLE IF NOT EXISTS component_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    component_id    INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    path            TEXT NOT NULL,
    is_test         INTEGER NOT NULL DEFAULT 0,
    UNIQUE(component_id, path)
);

-- L2 module-to-module dependencies (from Part 1)
CREATE TABLE IF NOT EXISTS module_edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    target_id   INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    edge_type   TEXT,
    weight      REAL DEFAULT 1.0,
    metadata    TEXT,
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    UNIQUE(source_id, target_id, edge_type)
);

-- L3 component-to-component dependencies (from Part 2)
CREATE TABLE IF NOT EXISTS component_edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    target_id   INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    edge_type   TEXT,
    weight      REAL DEFAULT 1.0,
    metadata    TEXT,
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    UNIQUE(source_id, target_id, edge_type)
);

-- Technical decisions — polymorphic FK to module OR component.
-- A decision is "attached" when exactly one of module_id/component_id is set.
-- Both NULL = "parked" (orphaned): its home entity was reaped by the pipeline
-- but the decision is human/chat-authored, so we detach and remember where it
-- lived via orphaned_module_name (+ orphaned_component_name for component
-- decisions) so it can be re-attached if the entity reappears.
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id       INTEGER REFERENCES modules(id) ON DELETE CASCADE,
    component_id    INTEGER REFERENCES components(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,
    text            TEXT NOT NULL,
    detail          TEXT,
    source          TEXT NOT NULL DEFAULT 'pipeline_generated',
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    orphaned_module_name    TEXT,
    orphaned_component_name TEXT,
    CHECK (NOT (module_id IS NOT NULL AND component_id IS NOT NULL))
);

-- Change records — tracks every map edit (decisions, modules, components)
CREATE TABLE IF NOT EXISTS change_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type     TEXT NOT NULL,
    entity_id       INTEGER NOT NULL,
    action          TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    origin          TEXT NOT NULL,
    baseline_id     INTEGER REFERENCES baselines(id),
    module_id       INTEGER,
    component_id    INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Baselines — marks ticket generation boundaries
CREATE TABLE IF NOT EXISTS baselines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Implementation tickets from change records
CREATE TABLE IF NOT EXISTS tickets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    acceptance_criteria TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    pipeline_run_id     INTEGER REFERENCES pipeline_runs(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Files affected by a ticket
CREATE TABLE IF NOT EXISTS ticket_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    UNIQUE(ticket_id, path)
);

-- Change-record-to-ticket traceability
CREATE TABLE IF NOT EXISTS ticket_change_records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id           INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    change_record_id    INTEGER NOT NULL REFERENCES change_records(id) ON DELETE CASCADE,
    UNIQUE(ticket_id, change_record_id)
);

-- Decision-to-ticket traceability (direct link to source decisions)
CREATE TABLE IF NOT EXISTS ticket_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    decision_id INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    UNIQUE(ticket_id, decision_id)
);

-- Map version snapshots — captures the full decision state at a point in time
CREATE TABLE IF NOT EXISTS map_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version_number  INTEGER NOT NULL,
    trigger         TEXT NOT NULL,
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    summary         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(version_number)
);

-- Frozen copy of all decisions at a specific version
CREATE TABLE IF NOT EXISTS version_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id      INTEGER NOT NULL REFERENCES map_versions(id) ON DELETE CASCADE,
    decision_id     INTEGER,
    module_id       INTEGER,
    component_id    INTEGER,
    module_name     TEXT,
    component_name  TEXT,
    category        TEXT NOT NULL,
    text            TEXT NOT NULL,
    source          TEXT NOT NULL
);

-- Re-validation runs — links before/after version pairs
CREATE TABLE IF NOT EXISTS validation_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_run_id     INTEGER REFERENCES pipeline_runs(id),
    before_version_id   INTEGER NOT NULL REFERENCES map_versions(id),
    after_version_id    INTEGER REFERENCES map_versions(id),
    model               TEXT,
    status              TEXT NOT NULL DEFAULT 'running',
    summary             TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Per-decision validation outcomes from re-validation runs
CREATE TABLE IF NOT EXISTS decision_validations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    validation_run_id   INTEGER NOT NULL REFERENCES validation_runs(id) ON DELETE CASCADE,
    decision_id         INTEGER,
    source              TEXT NOT NULL,
    status              TEXT NOT NULL,
    old_text            TEXT,
    new_text            TEXT,
    reason              TEXT,
    category            TEXT,
    module_name         TEXT,
    component_name      TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(validation_run_id, decision_id)
);
"""
