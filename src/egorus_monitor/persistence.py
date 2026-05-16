from __future__ import annotations

import queue
import threading
from dataclasses import dataclass

from egorus_monitor.domain import FaultEvent, HealthSnapshot, TelemetryPoint
from egorus_monitor.influxdb import InfluxManager


@dataclass(slots=True)
class PersistenceBatch:
    points: list[TelemetryPoint]
    snapshots: list[HealthSnapshot]
    events: list[FaultEvent]


class AsyncPersistenceWorker:
    def __init__(self, influx: InfluxManager, max_queue: int = 12) -> None:
        self.influx = influx
        self._queue: queue.Queue[PersistenceBatch | None] = queue.Queue(maxsize=max_queue)
        self._thread: threading.Thread | None = None
        self._running = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._running.set()
        self._thread = threading.Thread(target=self._run, name="egorus-influx-writer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._running.clear()
        self._offer(None)
        if self._thread:
            self._thread.join(timeout=timeout)
        self._thread = None

    def enqueue(self, points: list[TelemetryPoint], snapshots: list[HealthSnapshot], events: list[FaultEvent]) -> None:
        if not self.running:
            return
        self._offer(PersistenceBatch(list(points), list(snapshots), list(events)))

    def _offer(self, batch: PersistenceBatch | None) -> None:
        try:
            self._queue.put_nowait(batch)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(batch)
            except queue.Full:
                pass

    def _run(self) -> None:
        while self._running.is_set() or not self._queue.empty():
            try:
                batch = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if batch is None:
                continue
            if batch.points:
                self.influx.write_telemetry(batch.points)
            if batch.snapshots:
                self.influx.write_health(batch.snapshots)
            if batch.events:
                self.influx.write_events(batch.events)
