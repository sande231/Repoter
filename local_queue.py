"""SQLite-backed local telemetry queue.

Use this when telemetry cannot be delivered to the ingestion server immediately.
The queue is safe for concurrent threads and processes that share one DB file.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
import json
import os
import random
import sqlite3
import sys
import threading
import time
from typing import Any, TypeVar


T = TypeVar("T")


class LocalQueueError(RuntimeError):
    """Raised when the local telemetry queue cannot complete an operation."""


class LocalQueue:
    """Persist telemetry payloads locally until they can be retried.

    `dequeue()` returns the oldest pending payload and marks it as sent. Use
    `clear_sent()` after the caller has successfully replayed sent items.
    """

    def __init__(
        self,
        db_path: str = "telemetry_queue.db",
        *,
        busy_timeout_ms: int = 5000,
        max_retries: int = 5,
        retry_base_delay: float = 0.05,
        max_pool_size: int = 5,
    ) -> None:
        self.db_path = db_path
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.max_retries = max(0, int(max_retries))
        self.retry_base_delay = max(0.0, float(retry_base_delay))
        self.max_pool_size = max(1, int(max_pool_size))
        self._pool: list[sqlite3.Connection] = []
        self._pool_lock = threading.RLock()
        self._pid = os.getpid()
        if self.busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self._init_db()

    @contextmanager
    def _connection(self, *, write: bool = False, transaction: bool = True):
        """Checkout a pooled connection and commit or roll back safely."""
        conn = self._checkout_connection()
        discard = False
        try:
            if transaction:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            if transaction:
                conn.commit()
        except Exception:
            if transaction and conn.in_transaction:
                conn.rollback()
            discard = isinstance(self._current_exception(), sqlite3.Error)
            raise
        finally:
            if discard:
                self._discard_connection(conn)
            else:
                self._return_connection(conn)

    def __enter__(self) -> "LocalQueue":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @staticmethod
    def _current_exception() -> BaseException | None:
        return sys.exc_info()[1]

    def _ensure_db_dir(self) -> None:
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    def _open_connection(self) -> sqlite3.Connection:
        self._ensure_db_dir()
        conn = sqlite3.connect(
            self.db_path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _checkout_connection(self) -> sqlite3.Connection:
        with self._pool_lock:
            current_pid = os.getpid()
            if current_pid != self._pid:
                self.close()
                self._pid = current_pid

            if self._pool:
                return self._pool.pop()

        return self._open_connection()

    def _return_connection(self, conn: sqlite3.Connection) -> None:
        with self._pool_lock:
            if os.getpid() != self._pid or len(self._pool) >= self.max_pool_size:
                conn.close()
                return
            self._pool.append(conn)

    @staticmethod
    def _discard_connection(conn: sqlite3.Connection) -> None:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    def close(self) -> None:
        """Close all pooled connections held by this process."""
        with self._pool_lock:
            while self._pool:
                conn = self._pool.pop()
                self._discard_connection(conn)

    @staticmethod
    def _is_locked_error(exc: sqlite3.OperationalError) -> bool:
        message = str(exc).lower()
        return "database is locked" in message or "database table is locked" in message

    def _init_db(self) -> None:
        def enable_wal(conn: sqlite3.Connection) -> None:
            conn.execute("PRAGMA journal_mode=WAL").fetchone()

        def create_schema(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at INTEGER NOT NULL,
                    sent_at INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_telemetry_queue_status_id
                ON telemetry_queue (status, id)
                """
            )

        self._execute(enable_wal, write=False, transaction=False)
        self._execute(create_schema, write=True)

    def _retry_delay(self, attempt: int) -> float:
        delay_cap = self.retry_base_delay * (2**attempt)
        return random.uniform(0, delay_cap)

    def _execute(
        self,
        operation: Callable[[sqlite3.Connection], T],
        *,
        write: bool = False,
        transaction: bool = True,
    ) -> T:
        last_locked_error: sqlite3.OperationalError | None = None

        for attempt in range(self.max_retries + 1):
            try:
                with self._connection(write=write, transaction=transaction) as conn:
                    return operation(conn)
            except sqlite3.OperationalError as exc:
                if self._is_locked_error(exc) and attempt < self.max_retries:
                    last_locked_error = exc
                    time.sleep(self._retry_delay(attempt))
                    continue
                message = "SQLite local queue operation failed"
                if self._is_locked_error(exc):
                    message = (
                        "SQLite local queue is locked after "
                        f"{attempt + 1} attempts"
                    )
                raise LocalQueueError(f"{message}: {exc}") from exc
            except sqlite3.Error as exc:
                raise LocalQueueError(
                    f"SQLite local queue operation failed: {exc}"
                ) from exc

        raise LocalQueueError(
            f"SQLite local queue is locked after {self.max_retries + 1} attempts: "
            f"{last_locked_error}"
        )

    def enqueue(self, data: dict[str, Any]) -> int:
        """Store a telemetry payload and return its queue row id."""
        try:
            payload = json.dumps(data, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise LocalQueueError(
                "Telemetry payload must be JSON serializable"
            ) from exc

        def insert_payload(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(
                """
                INSERT INTO telemetry_queue (payload, status, created_at)
                VALUES (?, 'pending', ?)
                """,
                (payload, int(time.time())),
            )
            return int(cursor.lastrowid)

        return self._execute(insert_payload, write=True)

    def dequeue(self) -> dict[str, Any] | None:
        """Return the oldest pending payload and mark it as sent."""
        def pop_pending(conn: sqlite3.Connection) -> dict[str, Any] | None:
            row = conn.execute(
                """
                SELECT id, payload
                FROM telemetry_queue
                WHERE status = 'pending'
                ORDER BY id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None

            conn.execute(
                """
                UPDATE telemetry_queue
                SET status = 'sent', sent_at = ?
                WHERE id = ?
                """,
                (int(time.time()), row["id"]),
            )
            return json.loads(row["payload"])

        try:
            return self._execute(pop_pending, write=True)
        except json.JSONDecodeError as exc:
            raise LocalQueueError(
                f"Queued telemetry row contains invalid JSON: {exc}"
            ) from exc

    def get_pending_count(self) -> int:
        """Return the number of payloads waiting to be replayed."""
        def count_pending(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM telemetry_queue WHERE status = 'pending'"
            ).fetchone()
            return int(row["count"])

        return self._execute(count_pending, write=False, transaction=False)

    def clear_sent(self) -> int:
        """Delete sent rows and return the number of rows removed."""
        def delete_sent(conn: sqlite3.Connection) -> int:
            cursor = conn.execute("DELETE FROM telemetry_queue WHERE status = 'sent'")
            return int(cursor.rowcount)

        return self._execute(delete_sent, write=True)

    def requeue_sent(self) -> int:
        """Move sent rows back to pending after a replay failure."""
        def reset_sent(conn: sqlite3.Connection) -> int:
            cursor = conn.execute(
                """
                UPDATE telemetry_queue
                SET status = 'pending', sent_at = NULL
                WHERE status = 'sent'
                """
            )
            return int(cursor.rowcount)

        return self._execute(reset_sent, write=True)
