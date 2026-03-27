from __future__ import annotations

import atexit
import threading
from datetime import datetime
from typing import Any, Dict

from config import SCHEDULER_INTERVAL_SECONDS
from services.reservation_service import reservation_service


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


class ReservationScheduler:
    def __init__(self, interval_seconds: int = SCHEDULER_INTERVAL_SECONDS):
        self.interval_seconds = max(int(interval_seconds), 5)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self.last_tick_at: str | None = None
        self.last_result: Dict[str, Any] | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(target=self._run, name='reservation-scheduler', daemon=True)
            self._thread.start()
            self._running = True

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._wake_event.set()
            self._running = False
        if self._thread:
            self._thread.join(timeout=1)

    def update_interval(self, seconds: int) -> int:
        with self._lock:
            self.interval_seconds = max(int(seconds), 5)
            self._wake_event.set()
            return self.interval_seconds

    def snapshot(self) -> Dict[str, Any]:
        return {
            'running': self._running,
            'intervalSeconds': self.interval_seconds,
            'lastTickAt': self.last_tick_at,
            'lastResult': self.last_result,
            'lastError': self.last_error,
        }

    def wake(self) -> None:
        self._wake_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.last_tick_at = now_iso()
            wait_seconds = self.interval_seconds
            try:
                self.last_result = reservation_service.process_due_tasks()
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
            else:
                try:
                    wait_seconds = reservation_service.next_poll_delay_seconds(self.interval_seconds)
                except Exception:  # noqa: BLE001
                    wait_seconds = self.interval_seconds

            self._wake_event.wait(wait_seconds)
            self._wake_event.clear()


reservation_scheduler = ReservationScheduler()
atexit.register(reservation_scheduler.stop)
