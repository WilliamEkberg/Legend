# Backend Tests

Unit and integration tests for the backend Python code.

## What's tested

| File | Module under test | What it covers |
|------|-------------------|----------------|
| `test_db.py` | `db.py` | All CRUD operations: modules, components, edges, decisions, change records, baselines, tickets |
| `test_db_export.py` | `db.export_full_map()` | Nested JSON export structure, metadata parsing, edge cases |
| `test_llm_parse.py` | `component_discovery/llm_client.py`, `map_descriptions/llm_client.py` | `_parse_json_response()` JSON extraction fallbacks (plain, code-fenced, embedded) |
| `test_edge_aggregator.py` | `component_discovery/edge_aggregator.py` | `aggregate_component_edges()` file→component edge rollup |
| `test_test_filter.py` | `component_discovery/test_filter.py` | `is_test_file()` and `split_source_and_test()` pattern matching |
| `test_scip_helpers.py` | `component_discovery/scip_filter.py` | Pure helpers: symbol roles, callable detection, file matching |
| `test_collapse_tickets.py` | `main._collapse_changes()`, `main._build_ticket_prompt()` | Net-delta collapse logic, prompt construction |
| `test_api_endpoints.py` | `main.py` (FastAPI) | `/api/map`, decision CRUD, `/api/change-records`, `/api/tickets` |
| `test_docker_helpers.py` | `main.py` (Docker functions) | `_docker_image_exists()`, `_pull_docker_image()`, `_ensure_docker_image()`, `_get_scip_cmd()` |

## How to run

```bash
cd backend
bash tests/run.sh
```

Or directly with pytest:

```bash
cd backend
python -m pytest tests/ -v
```

## Requirements

- `pytest`
- `httpx` (for FastAPI TestClient)

No API keys, Docker, or external services needed.
