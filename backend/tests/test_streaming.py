"""Tests for streaming.py (subprocess-output drain helpers).

Async tests run via asyncio.run(...) inside plain sync functions
(pytest-asyncio is intentionally NOT a dependency).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streaming import merge_process_output, MAX_LINE_LEN


def _make_reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def test_drain_survives_line_over_64kb():
    """A line far larger than the StreamReader limit must not kill the pump;
    the drain still terminates, emits a truncation marker, and later lines
    ('ok') still come through."""
    async def run():
        reader = _make_reader(b"x" * 200000 + b"\nok\n")
        out = []
        async for etype, text in merge_process_output([(reader, "[T]")]):
            out.append((etype, text))
        return out

    out = asyncio.run(run())
    texts = [t for _e, t in out]
    # Truncation marker present
    assert any("[line truncated]" in t for t in texts)
    # Clipped chunks never exceed MAX_LINE_LEN (plus label + marker slack)
    for t in texts:
        assert len(t) < MAX_LINE_LEN + 100
    # The short line after the giant one still arrives
    assert any(t.endswith("ok") for t in texts)


def test_sentinel_posted_even_when_consumer_exits_early():
    """Closing the consumer early must cancel + await the pump tasks so no
    orphan tasks are left running."""
    async def run():
        # A stream that never hits EOF would block forever without cleanup.
        reader = asyncio.StreamReader()
        reader.feed_data(b"line1\nline2\n")  # NOTE: no feed_eof

        gen = merge_process_output([(reader, "[T]")])
        first = await gen.__anext__()
        await gen.aclose()
        # Only the current task should remain (pumps cancelled + awaited).
        alive = [t for t in asyncio.all_tasks() if not t.done()]
        return first, len(alive)

    first, alive = asyncio.run(run())
    assert first == ("stdout", "[T] line1")
    assert alive == 1


def test_merge_multiple_sources_all_eof():
    """Merge terminates only when EVERY source reaches EOF, and all lines are
    delivered."""
    async def run():
        r1 = _make_reader(b"a1\na2\n")
        r2 = _make_reader(b"b1\n")
        out = []
        async for etype, text in merge_process_output(
            [(r1, "[A]"), (r2, "[B]")], event_type="stderr"
        ):
            out.append((etype, text))
        return out

    out = asyncio.run(run())
    texts = {t for _e, t in out}
    assert all(e == "stderr" for e, _t in out)
    assert texts == {"[A] a1", "[A] a2", "[B] b1"}
