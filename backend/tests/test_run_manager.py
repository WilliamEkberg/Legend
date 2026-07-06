"""Tests for run_manager.py (single-flight, cancel, deferred release) and the
run-lifecycle HTTP surface (409 rejection, status, cancel).

Async tests run via asyncio.run(...) in plain sync functions.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from run_manager import run_manager, RunInProgressError, guarded_sse_stream, sse


class FakeProcess:
    """Minimal asyncio-subprocess stand-in: terminate/kill set the exit event."""

    def __init__(self, returncode=None):
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self._exited = asyncio.Event()

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self._exited.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._exited.set()

    async def wait(self):
        await self._exited.wait()
        return self.returncode


@pytest.fixture(autouse=True)
def _reset_manager():
    run_manager.reset()
    yield
    run_manager.reset()


# ---------------------------------------------------------------------------
# Single-flight acquire
# ---------------------------------------------------------------------------

def test_try_acquire_second_run_raises():
    run_manager.try_acquire(step="part1", repo_path="/repo")
    with pytest.raises(RunInProgressError) as exc:
        run_manager.try_acquire(step="part2", repo_path="/repo")
    info = exc.value.run_info
    assert info["step"] == "part1"
    assert info["repo_path"] == "/repo"


def test_status_reports_active_and_inactive():
    assert run_manager.status()["active"] is False
    run_manager.try_acquire(step="part1", repo_path="/repo")
    st = run_manager.status()
    assert st["active"] is True
    assert st["run"]["step"] == "part1"


# ---------------------------------------------------------------------------
# Cancel + release
# ---------------------------------------------------------------------------

def test_cancel_terminates_registered_processes():
    async def run():
        handle = run_manager.try_acquire(step="part1", repo_path="/repo")
        p1, p2 = FakeProcess(), FakeProcess()
        handle.register_process(p1)
        handle.register_process(p2)
        cancelled = await run_manager.cancel()
        return cancelled, handle.cancel_requested, p1.terminated, p2.terminated

    cancelled, req, t1, t2 = asyncio.run(run())
    assert cancelled is True
    assert req is True
    assert t1 and t2


def test_cancel_when_idle_returns_false():
    assert asyncio.run(run_manager.cancel()) is False


def test_disconnect_kills_subprocesses_and_marks_run_cancelled(tmp_path):
    db_file = tmp_path / "test.db"
    conn = db.connect(str(db_file))
    db.init_schema(conn)
    run_id = db.start_pipeline_run(conn, "part1")
    db.close(conn)

    async def run():
        handle = run_manager.try_acquire(step="part1", repo_path="/repo")
        handle.set_pipeline_run_id(run_id)
        proc = FakeProcess()
        handle.register_process(proc)

        async def _inner():
            yield sse({"type": "stdout", "text": "working"})
            await asyncio.sleep(3600)  # blocks until the consumer disconnects
            yield sse({"type": "done", "success": True})

        gen = guarded_sse_stream(handle, _inner(), str(db_file))
        first = await gen.__anext__()
        await gen.aclose()  # simulate client disconnect
        return first, proc.terminated

    first, terminated = asyncio.run(run())
    assert "working" in first
    assert terminated is True
    # Slot released, run row finalized as cancelled.
    assert run_manager.status()["active"] is False
    conn = db.connect(str(db_file))
    row = conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    db.close(conn)
    assert row["status"] == "cancelled"


def test_release_deferred_until_thread_task_done(tmp_path):
    db_file = tmp_path / "test.db"
    conn = db.connect(str(db_file))
    db.init_schema(conn)
    run_id = db.start_pipeline_run(conn, "part2")
    db.close(conn)

    async def run():
        handle = run_manager.try_acquire(step="part2", repo_path="/repo")
        handle.set_pipeline_run_id(run_id)

        hold = asyncio.Event()

        async def _worker():
            await hold.wait()

        task = asyncio.create_task(_worker())
        handle.attach_task(task)

        # Release while the thread-backed task is still alive: slot stays held.
        await handle.release(db_status="cancelled", db_path=str(db_file))
        active_during = run_manager.status()["active"]

        # Finish the task -> deferred watcher finalizes + clears the slot.
        hold.set()
        await asyncio.sleep(0.05)
        active_after = run_manager.status()["active"]
        return active_during, active_after

    active_during, active_after = asyncio.run(run())
    assert active_during is True
    assert active_after is False
    conn = db.connect(str(db_file))
    row = conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    db.close(conn)
    assert row["status"] == "cancelled"


def test_normal_completion_does_not_touch_db(tmp_path):
    """db_status=None (normal completion) leaves the run row alone — each step
    finalizes its own row."""
    db_file = tmp_path / "test.db"
    conn = db.connect(str(db_file))
    db.init_schema(conn)
    run_id = db.start_pipeline_run(conn, "part1")
    db.complete_pipeline_run(conn, run_id, "completed")
    db.close(conn)

    async def run():
        handle = run_manager.try_acquire(step="part1", repo_path="/repo")
        handle.set_pipeline_run_id(run_id)

        async def _inner():
            yield sse({"type": "done", "success": True})

        gen = guarded_sse_stream(handle, _inner(), str(db_file))
        async for _chunk in gen:
            pass

    asyncio.run(run())
    conn = db.connect(str(db_file))
    row = conn.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    db.close(conn)
    assert row["status"] == "completed"  # untouched by release


# ---------------------------------------------------------------------------
# HTTP surface: 409 rejection + status/cancel endpoints
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient
    import main

    db_file = tmp_path / "test.db"
    original = main.DB_PATH
    main.DB_PATH = db_file
    conn = db.connect(str(db_file))
    db.init_schema(conn)
    db.close(conn)

    tc = TestClient(main.app)
    yield tc
    main.DB_PATH = original


def test_run_stream_second_concurrent_run_rejected_409(client):
    # Simulate an active run by acquiring the slot directly.
    run_manager.try_acquire(step="part1", repo_path="/repo")

    resp = client.post("/api/run/stream", json={"api_key": "sk-test", "step": "part2"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "run_in_progress"

    resp2 = client.post("/api/run", json={"api_key": "sk-test"})
    assert resp2.status_code == 409
    assert resp2.json()["detail"]["error"] == "run_in_progress"


def test_run_status_endpoint_reports_active_run(client):
    assert client.get("/api/run/status").json()["active"] is False
    run_manager.try_acquire(step="part3", repo_path="/repo")
    body = client.get("/api/run/status").json()
    assert body["active"] is True
    assert body["run"]["step"] == "part3"


def test_cancel_endpoint(client):
    assert client.post("/api/run/cancel").json()["cancelled"] is False
    run_manager.try_acquire(step="part1", repo_path="/repo")
    assert client.post("/api/run/cancel").json()["cancelled"] is True


def test_unknown_step_does_not_acquire_slot(client):
    resp = client.post("/api/run/stream", json={"api_key": "sk-test", "step": "bogus"})
    assert resp.status_code == 200  # SSE error stream, not 409
    # No slot was taken by the bad request.
    assert run_manager.status()["active"] is False
