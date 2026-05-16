from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests

from egorus_monitor.domain import FaultEvent, HealthSnapshot, TelemetryPoint


@dataclass(slots=True)
class InfluxConfig:
    host: str = "127.0.0.1"
    port: int = 8181
    database: str = "egorus"
    token: str = "egorus-local-admin-token"
    data_dir: Path = Path("runtime/influxdb/data")
    plugin_dir: Path = Path("runtime/influxdb/plugins")
    binary_path: Path = Path("vendor/influxdb3/influxdb3.exe")

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class InfluxManager:
    def __init__(self, config: InfluxConfig | None = None, root: Path | None = None) -> None:
        self.root = root or default_app_root()
        self.config = config or InfluxConfig()
        self.process: subprocess.Popen[str] | None = None
        self.last_error = ""
        self.enabled = False
        self._last_health_check = 0.0
        self._last_health_ok = False

    @property
    def binary_path(self) -> Path:
        path = self.config.binary_path
        if path.is_absolute():
            return path
        candidates = [
            self.root / path,
            self.root / "_internal" / path,
            Path(getattr(sys, "_MEIPASS", self.root)) / path,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def start(self) -> bool:
        """Попытка подключиться к внешнему серверу InfluxDB (например, в Docker)"""
        if self.health_check(force=True):
            self.enabled = True
            return True

        self.last_error = "InfluxDB недоступен по сети. Приложение переходит в локальный режим."
        self.enabled = False
        return False

    def stop(self) -> None:
        """Отключает запись в InfluxDB при закрытии приложения"""
        self.enabled = False
        self.process = None

    def health_check(self, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and now - self._last_health_check < 5.0:
            return self._last_health_ok
        self._last_health_check = now
        try:
            response = requests.get(f"{self.config.base_url}/health", timeout=0.18)
            self._last_health_ok = response.status_code < 500
            return self._last_health_ok
        except requests.RequestException:
            self._last_health_ok = False
            return False

    def write_telemetry(self, points: Iterable[TelemetryPoint]) -> bool:
        lines = [telemetry_line(point) for point in points]
        return self._write_lines(lines)

    def write_health(self, snapshots: Iterable[HealthSnapshot]) -> bool:
        lines = [health_line(snapshot) for snapshot in snapshots]
        return self._write_lines(lines)

    def write_events(self, events: Iterable[FaultEvent]) -> bool:
        lines = [event_line(event) for event in events]
        return self._write_lines(lines)

    def query_sql(self, sql: str) -> list[dict[str, object]]:
        try:
            response = requests.get(
                f"{self.config.base_url}/api/v3/query_sql",
                params={"db": self.config.database, "q": sql, "format": "json"},
                headers=self._headers(),
                timeout=2.0,
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                return [row for row in payload if isinstance(row, dict)]
            if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                return [row for row in payload["data"] if isinstance(row, dict)]
        except requests.RequestException as exc:
            self.last_error = f"SQL-запрос InfluxDB не выполнен: {exc}"
        except ValueError as exc:
            self.last_error = f"InfluxDB вернул неожиданный JSON: {exc}"
        return []

    def _write_lines(self, lines: list[str]) -> bool:
        if not lines:
            return True
        if not self.enabled and not self.health_check(force=True):
            return False

        try:
            response = requests.post(
                f"{self.config.base_url}/api/v3/write_lp",
                params={"db": self.config.database, "precision": "nanosecond"},
                headers=self._headers(content_type="text/plain; charset=utf-8"),
                data="\n".join(lines).encode("utf-8"),
                timeout=1.5,
            )
            response.raise_for_status()
            self.enabled = True
            return True
        except requests.RequestException as exc:
            self.last_error = f"Запись InfluxDB не выполнена: {exc}"
            self.enabled = False
            return False

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.config.token}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else self.root / path


def telemetry_line(point: TelemetryPoint) -> str:
    tags = _tags(
        equipment_id=point.equipment_id,
        sensor_id=point.sensor_id,
        source=point.source,
        scenario=point.scenario,
        fault_type=point.fault_type.value,
    )
    fields = _fields(
        vibration_rms=point.vibration_rms,
        crest_factor=point.crest_factor,
        a_spec=point.a_spec,
        rpm=point.rpm,
        temperature=point.temperature,
        one_x_amplitude=point.one_x_amplitude,
        two_x_amplitude=point.two_x_amplitude,
        bearing_band_energy=point.bearing_band_energy,
        broadband_energy=point.broadband_energy,
        signal_quality=point.signal_quality,
        packet_loss=point.packet_loss,
    )
    return f"telemetry,{tags} {fields} {_ts(point.timestamp)}"


def health_line(snapshot: HealthSnapshot) -> str:
    tags = _tags(
        equipment_id=snapshot.equipment_id,
        sensor_id=snapshot.sensor_id,
        source=snapshot.source,
        scenario=snapshot.scenario,
        fault_type=snapshot.fault_type.value,
        risk_zone=snapshot.risk_zone.value,
    )
    fields = _fields(
        hi=snapshot.hi,
        rul_hours=snapshot.rul_hours,
        confidence=snapshot.confidence,
        trend_per_hour=snapshot.trend_per_hour,
        diagnosis=snapshot.diagnosis,
        recommendation=snapshot.recommendation,
    )
    return f"health,{tags} {fields} {_ts(snapshot.timestamp)}"


def event_line(event: FaultEvent) -> str:
    tags = _tags(
        equipment_id=event.equipment_id,
        sensor_id=event.sensor_id,
        risk_zone=event.risk_zone.value,
        fault_type=event.fault_type.value,
    )
    fields = _fields(title=event.title, details=event.details, acknowledged=event.acknowledged)
    return f"events,{tags} {fields} {_ts(event.timestamp)}"


def _tags(**items: str) -> str:
    return ",".join(f"{_escape_tag(k)}={_escape_tag(v)}" for k, v in items.items())


def _fields(**items: object) -> str:
    chunks: list[str] = []
    for key, value in items.items():
        if isinstance(value, bool):
            chunks.append(f"{key}={'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            chunks.append(f"{key}={float(value):.8g}")
        else:
            chunks.append(f'{key}="{_escape_string(str(value))}"')
    return ",".join(chunks)


def _escape_tag(value: str) -> str:
    return str(value).replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _escape_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _ts(timestamp) -> int:
    return int(timestamp.timestamp() * 1_000_000_000)


def default_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()
