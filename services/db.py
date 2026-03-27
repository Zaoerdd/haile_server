from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from config import DATABASE_FILE


class Database:
    def __init__(self, path: Path = DATABASE_FILE):
        self.path = Path(path)
        self._lock = threading.Lock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reservation_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            machine_source TEXT NOT NULL,
            machine_id TEXT NOT NULL,
            machine_name TEXT NOT NULL,
            room_id TEXT,
            room_name TEXT,
            qr_code TEXT,
            mode_id INTEGER NOT NULL,
            mode_name TEXT NOT NULL,
            schedule_type TEXT NOT NULL,
            target_time TEXT NOT NULL,
            weekday INTEGER,
            time_of_day TEXT,
            timezone_name TEXT,
            lead_minutes INTEGER NOT NULL,
            status TEXT NOT NULL,
            active_order_no TEXT,
            start_at TEXT,
            hold_until TEXT,
            last_checked_at TEXT,
            last_error TEXT,
            current_order_snapshot TEXT,
            last_run_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_reservation_tasks_status
        ON reservation_tasks(status);

        CREATE INDEX IF NOT EXISTS idx_reservation_tasks_machine
        ON reservation_tasks(machine_source, machine_id);

        CREATE TABLE IF NOT EXISTS reservation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            payload TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES reservation_tasks(id)
        );

        CREATE INDEX IF NOT EXISTS idx_reservation_events_task_created
        ON reservation_events(task_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS workflow_processes (
            process_id TEXT PRIMARY KEY,
            flow_type TEXT NOT NULL,
            qr_code TEXT NOT NULL,
            mode_id INTEGER NOT NULL,
            current_step INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            terminated INTEGER NOT NULL DEFAULT 0,
            blocked_reason TEXT,
            goods_id TEXT,
            hash_key TEXT,
            order_no TEXT,
            prepay_param TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_workflow_processes_active
        ON workflow_processes(completed, terminated, order_no, updated_at DESC);
        """
        with self._lock:
            with self.connect() as connection:
                connection.executescript(schema)
                self._ensure_column(connection, 'reservation_tasks', 'current_order_snapshot', 'TEXT')
                self._ensure_column(connection, 'reservation_tasks', 'timezone_name', 'TEXT')

    def _ensure_column(self, connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing_columns = {
            str(row['name'])
            for row in connection.execute(f'PRAGMA table_info({table})')
        }
        if column in existing_columns:
            return
        connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

    def fetch_one(self, query: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self.connect() as connection:
            cursor = connection.execute(query, params)
            return cursor.fetchone()

    def fetch_all(self, query: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self.connect() as connection:
            cursor = connection.execute(query, params)
            return list(cursor.fetchall())

    def execute(self, query: str, params: Sequence[Any] = ()) -> int:
        with self._lock:
            with self.connect() as connection:
                cursor = connection.execute(query, params)
                return int(cursor.lastrowid or 0)

    def execute_many(self, query: str, rows: Sequence[Sequence[Any]]) -> None:
        with self._lock:
            with self.connect() as connection:
                connection.executemany(query, rows)


database = Database()
