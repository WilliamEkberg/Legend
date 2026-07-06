"""Validation boundary for LLM-produced JSON. Runs BEFORE any DB mutation.

Two responsibilities:
1. Structural validation of the modules JSON produced by Part 1 (opencode),
   so malformed/duplicate output is rejected with a clear error instead of
   exploding on a UNIQUE constraint mid-write.
2. Robust JSON extraction from free-form LLM text (fenced blocks, prose,
   truncation) for the ticket-generation path.
"""

import json


class LLMOutputError(ValueError):
    """Raised when LLM output fails structural validation / cannot be parsed."""


def validate_l2_modules(data) -> list[dict]:
    """Validate the modules JSON (Part 1 / /api/run).

    Returns a list of normalized entries with keys: id, name, classification,
    type, technology, source_origin, deployment_target, directories,
    relationships, consumedBy.

    Raises LLMOutputError listing every problem found.
    """
    problems: list[str] = []

    if not isinstance(data, dict):
        raise LLMOutputError("top-level JSON must be an object with a 'modules' key")
    modules = data.get("modules")
    if not isinstance(modules, list):
        raise LLMOutputError("missing or non-list 'modules'")

    normalized: list[dict] = []
    seen_names: set[str] = set()
    seen_ids: set[str] = set()

    for i, entry in enumerate(modules):
        if not isinstance(entry, dict):
            problems.append(f"modules[{i}]: not an object")
            continue

        raw_name = entry.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            problems.append(f"modules[{i}]: missing 'name'")
            name = None
        else:
            name = raw_name.strip()
            if name in seen_names:
                problems.append(f"modules[{i}]: duplicate name '{name}'")
            seen_names.add(name)

        raw_id = entry.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            problems.append(f"modules[{i}]: missing 'id'")
            ent_id = None
        else:
            ent_id = raw_id.strip()
            if ent_id in seen_ids:
                problems.append(f"modules[{i}]: duplicate id '{ent_id}'")
            seen_ids.add(ent_id)

        directories = entry.get("directories")
        if directories is None:
            directories = []
        elif not isinstance(directories, list) or not all(isinstance(d, str) for d in directories):
            problems.append(f"modules[{i}]: 'directories' must be a list of strings")
            directories = []

        def _coerce_str(v):
            return v if isinstance(v, str) else None

        classification = _coerce_str(entry.get("classification")) or "module"

        normalized.append({
            "id": ent_id,
            "name": name,
            "classification": classification,
            "type": _coerce_str(entry.get("type")),
            "technology": _coerce_str(entry.get("technology")),
            "source_origin": _coerce_str(entry.get("sourceOrigin")),
            "deployment_target": _coerce_str(entry.get("deploymentTarget")),
            "directories": directories,
            "relationships": entry.get("relationships", []),
            "consumedBy": entry.get("consumedBy", []),
        })

    if problems:
        raise LLMOutputError("; ".join(problems))

    return normalized


def parse_llm_json(text, expect: str = "object"):
    """Robustly extract JSON from an LLM response.

    expect: "object" -> require a top-level dict; "array" -> require a list.
    Tries, in order: plain json.loads; ```json fenced blocks; generic ```
    fenced blocks; a bracket scan (first opener .. matching last closer).
    Raises LLMOutputError on None/empty/unparseable/wrong-type output.
    """
    if text is None:
        raise LLMOutputError("LLM returned no content")
    if not isinstance(text, str):
        raise LLMOutputError(f"expected string content, got {type(text).__name__}")

    stripped = text.strip()
    if not stripped:
        raise LLMOutputError("LLM returned empty content")

    def _accepts(value) -> bool:
        if expect == "array":
            return isinstance(value, list)
        return isinstance(value, dict)

    candidates: list[str] = []

    # 1. Plain JSON
    candidates.append(stripped)

    # 2. ```json fenced blocks (try each, in order)
    marker = "```json"
    idx = stripped.find(marker)
    while idx != -1:
        start = idx + len(marker)
        end = stripped.find("```", start)
        if end == -1:
            break
        candidates.append(stripped[start:end].strip())
        idx = stripped.find(marker, end)

    # 3. Generic ``` fenced blocks (with optional language tag on first line)
    idx = stripped.find("```")
    while idx != -1:
        start = idx + 3
        newline = stripped.find("\n", start)
        end = stripped.find("```", start)
        if end == -1:
            break
        block_start = start
        if newline != -1 and newline < end:
            block_start = newline + 1
        candidates.append(stripped[block_start:end].strip())
        idx = stripped.find("```", end + 3)

    # Try structured candidates first
    for cand in candidates:
        if not cand:
            continue
        try:
            value = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if _accepts(value):
            return value

    # 4. Bracket scan fallback
    if expect == "array":
        opener, closer = "[", "]"
    else:
        opener, closer = "{", "}"
    start = stripped.find(opener)
    end = stripped.rfind(closer)
    if start != -1 and end > start:
        try:
            value = json.loads(stripped[start:end + 1])
            if _accepts(value):
                return value
        except json.JSONDecodeError:
            pass

    raise LLMOutputError(
        f"could not parse a JSON {expect} from response: {stripped[:200]}..."
    )
