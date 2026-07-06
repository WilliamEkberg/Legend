"""Shared helpers for draining subprocess output into SSE streams.

Replaces the ad-hoc drain patterns in main.py. Two bugs are fixed here:
(a) `async for line in stream` raises ValueError on lines larger than the
    asyncio StreamReader limit (64KB default), which kills the drain task
    BEFORE it posts its sentinel, so a `while received < expected` loop waits
    forever. We read in chunks and split lines manually, clipping long lines.
(b) fire-and-forget `create_task` pumps were never awaited or cancelled (GC
    could drop them). Here a single merge generator OWNS the pump tasks (strong
    refs) and cancels + awaits them in its own finally, which runs on consumer
    aclose()/disconnect.
"""

import asyncio
from typing import AsyncIterator

_STREAM_DONE = object()
MAX_LINE_LEN = 8192
CHUNK_SIZE = 65536


def _clip(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").rstrip("\r")[:MAX_LINE_LEN]


async def _pump_stream(stream, queue, label, event_type):
    """Read a pipe in chunks, split into lines, put (event_type, 'label text')
    on the queue. ALWAYS posts _STREAM_DONE via finally (put_nowait so it
    survives cancellation during teardown)."""
    buf = b""
    try:
        while True:
            chunk = await stream.read(CHUNK_SIZE)
            if not chunk:
                if buf.strip():
                    await queue.put((event_type, f"{label} {_clip(buf)}"))
                return
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for ln in lines:
                if ln.strip():
                    await queue.put((event_type, f"{label} {_clip(ln)}"))
            if len(buf) > CHUNK_SIZE:
                await queue.put((event_type, f"{label} {_clip(buf)} …[line truncated]"))
                buf = b""
    finally:
        try:
            queue.put_nowait(_STREAM_DONE)
        except asyncio.QueueFull:
            pass


async def merge_process_output(sources, event_type="stdout") -> AsyncIterator:
    """Merge (stream, label) sources into one ordered (event_type, text) stream.

    Owns the pump tasks; cancels + awaits them on exit (including GeneratorExit
    when the consumer disconnects). Terminates when every source hits EOF.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    tasks = [
        asyncio.create_task(_pump_stream(s, queue, label, event_type))
        for s, label in sources
    ]
    try:
        remaining = len(tasks)
        while remaining:
            item = await queue.get()
            if item is _STREAM_DONE:
                remaining -= 1
            else:
                yield item
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
