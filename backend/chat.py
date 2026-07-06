# Doc: Natural_Language_Code/chat/info_chat.md

"""Chat orchestration — MCP client, LLM loop, sessions, propose-then-confirm."""

import asyncio
import json
import sys
import uuid
from collections import OrderedDict
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

import litellm
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from mcp_server import WRITE_TOOL_NAMES

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_ASK = """\
You are an architecture assistant for the Legend architecture mapping tool.
You help users understand their codebase architecture by querying the architecture map database.

The architecture map contains:
- **Modules** (L2): Top-level architectural units — services, shared libraries, supporting assets. Each module owns specific directories in the codebase.
- **Components** (L3): Sub-units within modules, discovered via code analysis
- **Decisions**: Technical decisions attached to modules or components
- **Module Edges**: Dependencies between modules (depends_on, uses_data_store, communicates_via)
- **Component Edges**: Dependencies between components (depends-on, call, import, inheritance)

IMPORTANT: Modules with different directories are SEPARATE applications, even if they contain similarly-named components. Always check module directories to understand boundaries.

Recommended workflow:
1. Use get_map_overview to understand the structure and application boundaries
2. Use get_module to examine a specific module's metadata and decisions
3. Use get_module_components to see all components within a module
4. Use get_component for detailed view of a single component
5. Use edge tools to trace dependencies

Use the available tools to look up specific data when answering questions.
Always cite specific module/component names and IDs when referencing them.
When you mention a module or component, format it as: [Name](module:ID) or [Name](component:ID)
so the UI can highlight it on the graph.

Be concise but thorough. If the user asks about relationships or dependencies,
use the edge tools to find connections."""

SYSTEM_PROMPT_EDIT = SYSTEM_PROMPT_ASK + """

You are in EDIT mode. You can propose changes to the architecture map.
When the user asks you to make changes, call the appropriate write tools
(add_module, add_component, add_decision, update_decision, delete_decision,
add_module_edge, add_component_edge, delete_module, delete_component).

Each write tool call will be shown to the user for confirmation before applying.
Explain what each proposed change does and why."""


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@dataclass
class ProposedChange:
    id: str
    tool_name: str
    arguments: dict
    description: str
    status: str = "pending"  # pending | applied | rejected


@dataclass
class ChatSession:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    history: list[dict] = field(default_factory=list)
    pending_changes: list[ProposedChange] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)


MAX_SESSIONS = 20
_sessions: "OrderedDict[str, ChatSession]" = OrderedDict()


def get_or_create_session(session_id: str | None) -> ChatSession:
    if session_id and session_id in _sessions:
        _sessions.move_to_end(session_id)
        return _sessions[session_id]
    session = ChatSession()
    # LRU bound: evict least-recently-used sessions before inserting a new one.
    while len(_sessions) >= MAX_SESSIONS:
        _sessions.popitem(last=False)
    _sessions[session.id] = session
    return session


def delete_session(session_id: str) -> bool:
    return _sessions.pop(session_id, None) is not None


# ---------------------------------------------------------------------------
# MCP client management
# ---------------------------------------------------------------------------
# stdio_client(...) and ClientSession(...) are anyio context managers that hold
# cancel scopes. anyio requires __aenter__ and __aexit__ to run in the SAME
# task. The old code entered them in one request task and exited in another,
# raising "Attempted to exit cancel scope in a different task". Fix: a dedicated
# long-lived OWNER task performs BOTH enter and exit; requests only read the
# session and signal shutdown via an event.


class MCPManager:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._owner_task: asyncio.Task | None = None
        self._session: ClientSession | None = None
        self._stop: asyncio.Event | None = None
        self._start_error: BaseException | None = None

    async def get_session(self, db_path: str) -> ClientSession:
        async with self._lock:
            if (
                self._session is not None
                and self._owner_task is not None
                and not self._owner_task.done()
            ):
                # Restart-on-death: health-check the live session.
                try:
                    await asyncio.wait_for(self._session.list_tools(), 10)
                    return self._session
                except Exception:
                    await self._shutdown_locked()
            await self._start_locked(db_path)
            return self._session

    async def shutdown(self):
        async with self._lock:
            await self._shutdown_locked()

    async def _start_locked(self, db_path: str):
        ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._start_error = None
        self._owner_task = asyncio.create_task(
            self._owner(db_path, ready, self._stop), name="mcp-owner"
        )
        await ready.wait()
        if self._start_error is not None:
            err = self._start_error
            self._owner_task = None
            raise RuntimeError(f"MCP server failed to start: {err!r}")

    async def _owner(self, db_path: str, ready: asyncio.Event, stop: asyncio.Event):
        """THE owner task: enters and exits both anyio context managers itself."""
        try:
            async with AsyncExitStack() as stack:
                params = StdioServerParameters(
                    command=sys.executable,
                    args=[str(Path(__file__).parent / "mcp_server.py"), str(db_path)],
                )
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._session = session
                ready.set()
                await stop.wait()  # park; the CMs exit below in THIS task
        except BaseException as e:
            self._start_error = e
        finally:
            self._session = None
            ready.set()  # unblock the startup waiter if spawn failed

    async def _shutdown_locked(self):
        task = self._owner_task
        self._owner_task = None
        self._session = None
        if task is None:
            return
        if self._stop is not None:
            self._stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), 10)
        except (asyncio.TimeoutError, Exception):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


_mcp = MCPManager()


async def get_mcp_session(db_path: str) -> ClientSession:
    return await _mcp.get_session(db_path)


async def shutdown_mcp():
    await _mcp.shutdown()


# ---------------------------------------------------------------------------
# Tool format conversion
# ---------------------------------------------------------------------------

def mcp_tool_to_openai(tool) -> dict:
    """Convert an MCP Tool to OpenAI-style function definition for litellm."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.inputSchema,
        },
    }


def describe_change(tool_name: str, args: dict) -> str:
    """Generate a human-readable description of a proposed write operation."""
    if tool_name == "add_module":
        return f"Add module \"{args.get('name')}\" ({args.get('classification', 'module')})"
    elif tool_name == "add_component":
        return f"Add component \"{args.get('name')}\" to module {args.get('module_id')}"
    elif tool_name == "add_decision":
        target = f"module {args.get('module_id')}" if args.get("module_id") else f"component {args.get('component_id')}"
        return f"Add {args.get('category')} decision to {target}"
    elif tool_name == "update_decision":
        return f"Update decision {args.get('decision_id')}"
    elif tool_name == "delete_decision":
        return f"Delete decision {args.get('decision_id')}"
    elif tool_name == "add_module_edge":
        return f"Add {args.get('edge_type')} edge: module {args.get('source_id')} → module {args.get('target_id')}"
    elif tool_name == "add_component_edge":
        return f"Add {args.get('edge_type')} edge: component {args.get('source_id')} → component {args.get('target_id')}"
    elif tool_name == "delete_module":
        return f"Delete module {args.get('module_id')} (cascading)"
    elif tool_name == "delete_component":
        return f"Delete component {args.get('component_id')} (cascading)"
    return f"{tool_name}({json.dumps(args)})"


# ---------------------------------------------------------------------------
# Chat turn (streaming)
# ---------------------------------------------------------------------------

async def run_chat_turn(
    message: str,
    mode: str,
    session: ChatSession,
    db_path: str,
    api_key: str,
    provider: str,
    model: str | None,
) -> AsyncGenerator[dict, None]:
    """Run one chat turn with the LLM, yielding SSE event dicts."""

    mcp = await get_mcp_session(db_path)

    # Get tools from MCP server
    tools_response = await mcp.list_tools()
    available_tools = tools_response.tools

    # Filter by mode
    if mode == "ask":
        available_tools = [t for t in available_tools if t.name not in WRITE_TOOL_NAMES]

    litellm_tools = [mcp_tool_to_openai(t) for t in available_tools]

    # Build messages
    system_prompt = SYSTEM_PROMPT_EDIT if mode == "edit" else SYSTEM_PROMPT_ASK
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(session.history)
    messages.append({"role": "user", "content": message})

    # Determine model
    if not model:
        if provider == "anthropic":
            model = "anthropic/claude-sonnet-4-20250514"
        elif provider == "openai":
            model = "openai/gpt-4o"
        elif provider == "google":
            model = "google/gemini-2.5-pro"
        elif provider == "groq":
            model = "groq/llama-3.3-70b-versatile"
        else:
            model = f"{provider}/claude-sonnet-4-20250514"

    proposed_changes: list[ProposedChange] = []

    # Agentic loop
    max_iterations = 10
    for _ in range(max_iterations):
        try:
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=litellm_tools if litellm_tools else None,
                stream=True,
                api_key=api_key,
            )
        except Exception as e:
            error_msg = str(e)
            if not error_msg:
                error_msg = f"{type(e).__name__}: {repr(e)}"
            # Surface HTTP status from litellm exceptions
            status = getattr(e, "status_code", None)
            if status == 401:
                error_msg = f"Authentication failed — check your API key. ({error_msg})"
            elif status == 402 or status == 429:
                error_msg = f"API credit/rate limit exceeded — you may be out of credits. ({error_msg})"
            yield {"type": "error", "text": error_msg}
            return

        full_content = ""
        tool_calls_accum: dict[int, dict] = {}

        async for chunk in response:
            delta = chunk.choices[0].delta

            # Stream text
            if delta.content:
                full_content += delta.content
                yield {"type": "text", "content": delta.content}

            # Accumulate tool calls
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_accum:
                        tool_calls_accum[idx] = {
                            "id": tc.id or "",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.id:
                        tool_calls_accum[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_accum[idx]["function"]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls_accum[idx]["function"]["arguments"] += tc.function.arguments

        # No tool calls — done
        if not tool_calls_accum:
            # Save to session history
            session.history.append({"role": "user", "content": message})
            session.history.append({"role": "assistant", "content": full_content})
            break

        # Process tool calls
        tool_calls_list = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
                },
            }
            for tc in tool_calls_accum.values()
        ]

        messages.append({
            "role": "assistant",
            "content": full_content or None,
            "tool_calls": tool_calls_list,
        })

        for tc in tool_calls_list:
            tool_name = tc["function"]["name"]
            try:
                tool_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                tool_args = {}

            yield {"type": "tool_call", "name": tool_name, "arguments": tool_args}

            if mode == "edit" and tool_name in WRITE_TOOL_NAMES:
                # Propose, don't execute
                change = ProposedChange(
                    id=str(uuid.uuid4()),
                    tool_name=tool_name,
                    arguments=tool_args,
                    description=describe_change(tool_name, tool_args),
                )
                proposed_changes.append(change)
                yield {
                    "type": "proposed_change",
                    "change": {
                        "id": change.id,
                        "tool_name": change.tool_name,
                        "arguments": change.arguments,
                        "description": change.description,
                        "status": change.status,
                    },
                }
                result_text = json.dumps({"status": "proposed", "message": f"Proposed: {change.description}. Awaiting user confirmation."})
            else:
                # Execute read tool via MCP
                try:
                    mcp_result = await mcp.call_tool(tool_name, tool_args)
                    result_text = mcp_result.content[0].text if mcp_result.content else "{}"
                except Exception as e:
                    result_text = json.dumps({"error": str(e)})

                yield {"type": "tool_result", "name": tool_name, "result": result_text}

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_text,
            })
    else:
        yield {"type": "error", "text": "Max tool iterations reached"}

    # Store proposed changes in session
    if proposed_changes:
        session.pending_changes.extend(proposed_changes)

    yield {
        "type": "done",
        "session_id": session.id,
        "proposed_changes": [
            {
                "id": c.id,
                "tool_name": c.tool_name,
                "arguments": c.arguments,
                "description": c.description,
                "status": c.status,
            }
            for c in proposed_changes
        ],
    }


# ---------------------------------------------------------------------------
# Confirm proposed changes
# ---------------------------------------------------------------------------

async def confirm_changes(
    session_id: str,
    change_ids: list[str],
    db_path: str,
) -> list[dict]:
    """Execute previously proposed changes. Returns results per change."""
    session = _sessions.get(session_id)
    if not session:
        return [{"change_id": cid, "success": False, "error": "Session not found"} for cid in change_ids]

    mcp = await get_mcp_session(db_path)
    results = []

    for change in session.pending_changes:
        if change.id not in change_ids:
            continue
        if change.status != "pending":
            results.append({"change_id": change.id, "success": False, "error": "Already processed"})
            continue

        try:
            mcp_result = await mcp.call_tool(change.tool_name, change.arguments)
            result_text = mcp_result.content[0].text if mcp_result.content else "{}"
            change.status = "applied"
            results.append({"change_id": change.id, "success": True, "result": json.loads(result_text)})
        except Exception as e:
            change.status = "rejected"
            results.append({"change_id": change.id, "success": False, "error": str(e)})

    return results
