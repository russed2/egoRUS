from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol


class RiskZone(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"

    @property
    def title(self) -> str:
        return {
            RiskZone.A: "Штатная эксплуатация",
            RiskZone.B: "Зарождающийся дефект",
            RiskZone.C: "Планирование ремонта",
            RiskZone.D: "Критический риск",
        }[self]

    @property
    def color(self) -> str:
        return {
            RiskZone.A: "#35d07f",
            RiskZone.B: "#f0c64b",
            RiskZone.C: "#ff8a3d",
            RiskZone.D: "#ff4d5f",
        }[self]


class FaultType(str, Enum):
    NONE = "none"
    BEARING = "bearing"
    IMBALANCE = "imbalance"
    MISALIGNMENT = "misalignment"
    LOOSENESS = "looseness"
    NOISE_LOSS = "noise_loss"

    @property
    def label(self) -> str:
        return {
            FaultType.NONE: "Норма",
            FaultType.BEARING: "Износ подшипника",
            FaultType.IMBALANCE: "Дисбаланс ротора",
            FaultType.MISALIGNMENT: "Расцентровка муфты",
            FaultType.LOOSENESS: "Ослабление крепления",
            FaultType.NOISE_LOSS: "Помехи / потери связи",
        }[self]


@dataclass(slots=True)
class Equipment:
    id: str
    name: str
    kind: str
    location: str
    rated_rpm: float
    power_kw: float
    manufacturer: str = "EgoRUS Demo"


@dataclass(slots=True)
class Sensor:
    id: str
    equipment_id: str
    name: str
    sensor_type: str
    position: str
    adapter: str
    sample_rate_hz: int = 4096
    enabled: bool = True


@dataclass(slots=True)
class SpectrumSnapshot:
    frequencies: list[float]
    amplitudes: list[float]


@dataclass(slots=True)
class TelemetryPoint:
    timestamp: datetime
    equipment_id: str
    sensor_id: str
    source: str
    scenario: str
    fault_type: FaultType
    vibration_rms: float
    crest_factor: float
    a_spec: float
    rpm: float
    temperature: float
    one_x_amplitude: float
    two_x_amplitude: float
    bearing_band_energy: float
    broadband_energy: float
    signal_quality: float
    packet_loss: float = 0.0
    spectrum: SpectrumSnapshot | None = None

    @staticmethod
    def now(**kwargs: object) -> "TelemetryPoint":
        return TelemetryPoint(timestamp=datetime.now(timezone.utc), **kwargs)


@dataclass(slots=True)
class HealthSnapshot:
    timestamp: datetime
    equipment_id: str
    sensor_id: str
    source: str
    scenario: str
    fault_type: FaultType
    hi: float
    rul_hours: float
    risk_zone: RiskZone
    confidence: float
    diagnosis: str
    trend_per_hour: float
    recommendation: str


@dataclass(slots=True)
class FaultEvent:
    timestamp: datetime
    equipment_id: str
    sensor_id: str
    risk_zone: RiskZone
    fault_type: FaultType
    title: str
    details: str
    acknowledged: bool = False


@dataclass(slots=True)
class EquipmentState:
    equipment: Equipment
    sensor: Sensor
    telemetry: TelemetryPoint | None = None
    health: HealthSnapshot | None = None
    history: list[HealthSnapshot] = field(default_factory=list)
    telemetry_history: list[TelemetryPoint] = field(default_factory=list)


class DataAdapter(Protocol):
    name: str

    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def read(self) -> list[TelemetryPoint]:
        ...

    def set_fault(self, equipment_id: str, fault_type: FaultType) -> None:
        ...
