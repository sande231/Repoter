import os
import multiprocessing
import queue as queue_module
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from local_queue import LocalQueue


def _enqueue_payloads(db_path, process_index, count, result_queue):
    try:
        queue = LocalQueue(
            db_path,
            busy_timeout_ms=1000,
            max_retries=20,
            retry_base_delay=0.001,
        )
        for item_index in range(count):
            queue.enqueue(
                {"process_index": process_index, "item_index": item_index}
            )
        queue.close()
        result_queue.put("")
    except Exception as exc:
        result_queue.put(repr(exc))


def test_enqueue_persists_pending_count(tmp_path):
    db_path = tmp_path / "telemetry.db"
    queue = LocalQueue(str(db_path))

    row_id = queue.enqueue({"agent_id": "a1", "metrics": {"cpu": 10}})

    assert row_id == 1
    assert queue.get_pending_count() == 1

    reopened = LocalQueue(str(db_path))
    assert reopened.get_pending_count() == 1


def test_dequeue_returns_fifo_payload_and_marks_sent(tmp_path):
    queue = LocalQueue(str(tmp_path / "telemetry.db"))
    first = {"agent_id": "a1", "metrics": {"cpu": 10}}
    second = {"agent_id": "a2", "metrics": {"cpu": 20}}
    queue.enqueue(first)
    queue.enqueue(second)

    assert queue.dequeue() == first
    assert queue.get_pending_count() == 1
    assert queue.dequeue() == second
    assert queue.get_pending_count() == 0
    assert queue.dequeue() is None


def test_clear_sent_removes_only_dequeued_rows(tmp_path):
    queue = LocalQueue(str(tmp_path / "telemetry.db"))
    queue.enqueue({"agent_id": "a1"})
    queue.enqueue({"agent_id": "a2"})
    queue.dequeue()

    removed = queue.clear_sent()

    assert removed == 1
    assert queue.get_pending_count() == 1
    assert queue.dequeue() == {"agent_id": "a2"}


def test_requeue_sent_moves_dequeued_rows_back_to_pending(tmp_path):
    queue = LocalQueue(str(tmp_path / "telemetry.db"))
    queue.enqueue({"agent_id": "a1"})

    assert queue.dequeue() == {"agent_id": "a1"}
    assert queue.get_pending_count() == 0

    restored = queue.requeue_sent()

    assert restored == 1
    assert queue.get_pending_count() == 1
    assert queue.dequeue() == {"agent_id": "a1"}


def test_database_uses_wal_and_busy_timeout(tmp_path):
    queue = LocalQueue(str(tmp_path / "telemetry.db"), busy_timeout_ms=5000)

    with queue._connection(transaction=False) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5000


def test_enqueue_retries_when_database_is_locked(monkeypatch, tmp_path):
    db_path = str(tmp_path / "telemetry.db")
    queue = LocalQueue(
        db_path,
        busy_timeout_ms=1,
        max_retries=3,
        retry_base_delay=0.001,
    )
    blocker = sqlite3.connect(db_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    sleeps = []

    def release_lock(delay):
        sleeps.append(delay)
        blocker.rollback()

    monkeypatch.setattr("local_queue.time.sleep", release_lock)

    try:
        row_id = queue.enqueue({"agent_id": "a1"})
    finally:
        blocker.close()

    assert row_id == 1
    assert sleeps
    assert queue.get_pending_count() == 1


def test_multiprocessing_enqueue_is_safe(tmp_path):
    db_path = str(tmp_path / "telemetry.db")
    process_count = 4
    payloads_per_process = 20
    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue()
    processes = [
        ctx.Process(
            target=_enqueue_payloads,
            args=(db_path, process_index, payloads_per_process, result_queue),
        )
        for process_index in range(process_count)
    ]

    for process in processes:
        process.start()

    messages = []
    for _ in processes:
        try:
            messages.append(result_queue.get(timeout=10))
        except queue_module.Empty:
            messages.append("worker timed out")

    for process in processes:
        process.join(timeout=10)

    assert messages == [""] * process_count
    assert all(process.exitcode == 0 for process in processes)

    queue = LocalQueue(db_path)
    assert queue.get_pending_count() == process_count * payloads_per_process
