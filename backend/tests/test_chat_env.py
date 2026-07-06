"""Tests for chat.py process-global fixes: no os.environ mutation, per-request
API keys, LRU-bounded sessions, and MCP spawning via sys.executable.

Async tests run via asyncio.run(...) in plain sync functions.
"""

import asyncio
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat


def _text_chunk(content):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            delta=types.SimpleNamespace(content=content, tool_calls=None)
        )]
    )


def test_chat_turn_does_not_mutate_os_environ(monkeypatch):
    captured = {}

    class _Tools:
        tools = []

    class _MCP:
        async def list_tools(self):
            return _Tools()

    async def fake_get_mcp_session(db_path):
        return _MCP()

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        async def _gen():
            yield _text_chunk("Hello")

        return _gen()

    monkeypatch.setattr(chat, "get_mcp_session", fake_get_mcp_session)
    monkeypatch.setattr(chat.litellm, "acompletion", fake_acompletion)

    # Ensure the provider env var is absent so we can prove it isn't added.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    snapshot = dict(os.environ)

    async def run():
        session = chat.ChatSession()
        events = []
        async for ev in chat.run_chat_turn(
            message="hi",
            mode="ask",
            session=session,
            db_path="/tmp/x.db",
            api_key="sk-test-123",
            provider="anthropic",
            model="anthropic/claude",
        ):
            events.append(ev)
        return events

    events = asyncio.run(run())

    # os.environ untouched; provider key never injected.
    assert dict(os.environ) == snapshot
    assert "ANTHROPIC_API_KEY" not in os.environ
    # api_key was passed straight to litellm instead.
    assert captured.get("api_key") == "sk-test-123"
    # The turn produced streamed text + a done event.
    assert any(e.get("type") == "text" for e in events)
    assert any(e.get("type") == "done" for e in events)


def test_sessions_bounded_lru():
    chat._sessions.clear()
    created = [chat.get_or_create_session(None) for _ in range(chat.MAX_SESSIONS + 5)]
    assert len(chat._sessions) <= chat.MAX_SESSIONS
    # The most recently created session is still resident.
    last_id = created[-1].id
    assert last_id in chat._sessions
    # Re-fetching by id returns the same object and marks it most-recent.
    again = chat.get_or_create_session(last_id)
    assert again is created[-1]
    chat._sessions.clear()


def test_mcp_uses_sys_executable(monkeypatch):
    captured = {}

    def fake_stdio_client(params):
        captured["params"] = params
        raise RuntimeError("boom")  # fail fast so startup surfaces the error

    monkeypatch.setattr(chat, "stdio_client", fake_stdio_client)

    mgr = chat.MCPManager()

    async def run():
        try:
            await mgr.get_session("/tmp/x.db")
        except RuntimeError:
            return "raised"
        return "no-error"

    result = asyncio.run(run())
    assert result == "raised"
    assert captured["params"].command == sys.executable
    assert str(Path(chat.__file__).parent / "mcp_server.py") in captured["params"].args
