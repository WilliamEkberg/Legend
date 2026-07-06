"""Single-flight run lifecycle for the pipeline.

The whole backend runs on one uvicorn event loop, so `try_acquire` is a plain
SYNCHRONOUS check-and-set: there is no `await` between checking `_active` and
setting it, so no other coroutine can interleave and no lock is needed. A second
run while one is active is REJECTED (HTTP 409), never queued.

Cancellation: registered subprocesses are terminated/killed. Thread-backed work
(asyncio.to_thread) cannot be force-killed, so those steps run to completion
after a client disconnect; the run slot stays held until the thread finishes
(deferred clear) because the thread is still writing the DB.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import db


class RunInProgressError(Exception):
    """Raised by try_acquire when a run is already active."""

    def __init__(self, run_info: dict):
        super().__init__("A pipeline run is already in progress")
        self.run_info = run_info


class RunHandle:
    def __init__(self, manager: "RunManager", step: str, repo_path: str | None):
        self.manager = manager
        self.token = uuid.uuid4().hex
        self.step = step
        self.repo_path = repo_path
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.pipeline_run_id: int | None = None
        self._procs: list = []
        self._task: asyncio.Task | None = None
        self._cancel_event = asyncio.Event()

    # -- process / task tracking -------------------------------------------
    def register_process(self, proc) -> None:
        self._procs.append(proc)

    def attach_task(self, task: asyncio.Task) -> None:
        """Track a to_thread-backed pipeline task. Threads can't be killed, so
        release() defers freeing the slot until the task finishes."""
        self._task = task

    def set_pipeline_run_id(self, run_id: int) -> None:
        self.pipeline_run_id = run_id

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    # -- termination -------------------------------------------------------
    async def kill_processes(self, grace: float = 5.0) -> None:
        """terminate() every live registered proc; after grace, kill(); await
        each so the child is fully reaped."""
        live = [p for p in self._procs if p.returncode is None]
        for p in live:
            try:
                p.terminate()
            except ProcessLookupError:
                pass
        if not live:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*(p.wait() for p in live), return_exceptions=True),
                timeout=grace,
            )
        except asyncio.TimeoutError:
            for p in live:
                if p.returncode is None:
                    try:
                        p.kill()
                    except ProcessLookupError:
                        pass
            await asyncio.gather(*(p.wait() for p in live), return_exceptions=True)

    async def release(self, db_status: str | None = None, db_path: str | None = None) -> None:
        """Kill leftover procs, then either defer or clear the slot.

        If a thread-backed task is still alive, DEFER the slot release: the
        thread is still writing the DB, and freeing the slot early would let a
        new run interleave destructive writes. Otherwise finalize the DB row and
        clear the slot immediately.
        """
        await self.kill_processes()
        if self._task is not None and not self._task.done():
            self.manager._defer_clear(self, self._task, db_status, db_path)
            return
        self._finalize_db(db_status, db_path)
        self.manager._clear(self)

    def _finalize_db(self, db_status: str | None, db_path: str | None) -> None:
        if not (db_status and self.pipeline_run_id and db_path):
            return
        try:
            conn = db.connect(db_path)
            try:
                db.finalize_run_if_running(conn, self.pipeline_run_id, db_status)
            finally:
                db.close(conn)
        except Exception:
            pass  # best-effort; never mask the real stream error


class RunManager:
    def __init__(self):
        self._active: RunHandle | None = None
        self._watchers: set = set()  # strong refs to deferred-clear tasks

    def try_acquire(self, step: str, repo_path: str | None = None) -> RunHandle:
        """SYNC atomic check-and-set. Raises RunInProgressError if busy."""
        if self._active is not None:
            raise RunInProgressError(self._run_info(self._active))
        handle = RunHandle(self, step, repo_path)
        self._active = handle
        return handle

    def status(self) -> dict:
        return {
            "active": self._active is not None,
            "run": self._run_info(self._active) if self._active is not None else None,
        }

    async def cancel(self) -> bool:
        h = self._active
        if h is None:
            return False
        h._cancel_event.set()
        await h.kill_processes()
        return True

    def reset(self) -> None:
        """Clear all state (startup / tests)."""
        self._active = None
        self._watchers.clear()

    # -- internals ---------------------------------------------------------
    def _run_info(self, h: RunHandle) -> dict:
        return {
            "token": h.token,
            "step": h.step,
            "repo_path": h.repo_path,
            "started_at": h.started_at,
            "pipeline_run_id": h.pipeline_run_id,
            "cancel_requested": h.cancel_requested,
            "finishing": h._task is not None and not h._task.done(),
        }

    def _clear(self, handle: RunHandle) -> None:
        if self._active is handle:
            self._active = None

    def _defer_clear(self, handle: RunHandle, task: asyncio.Task,
                     db_status: str | None, db_path: str | None) -> None:
        async def _watch():
            try:
                await asyncio.gather(task, return_exceptions=True)
            finally:
                handle._finalize_db(db_status, db_path)
                self._clear(handle)

        w = asyncio.create_task(_watch())
        self._watchers.add(w)
        w.add_done_callback(self._watchers.discard)


run_manager = RunManager()


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def guarded_sse_stream(handle: RunHandle, inner, db_path: str):
    """Wrap a step's SSE generator so the run slot is always released.

    Re-raises GeneratorExit/CancelledError (a disconnect): the finally block
    still runs, releasing the slot and marking the run cancelled. Yielding after
    GeneratorExit would raise RuntimeError, so we never do.
    """
    db_status = None
    try:
        async for chunk in inner:
            yield chunk
    except (GeneratorExit, asyncio.CancelledError):
        db_status = "cancelled"
        raise
    except Exception as e:
        db_status = "failed"
        yield sse({"type": "error", "text": f"Internal error: {e}"})
        yield sse({"type": "done", "success": False})
    finally:
        await handle.release(db_status=db_status, db_path=db_path)
