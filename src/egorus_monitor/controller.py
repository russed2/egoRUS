from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from egorus_monitor.analytics import PredictiveAnalyzer
from egorus_monitor.domain import Equipment, EquipmentState, FaultEvent, FaultType, HealthSnapshot, RiskZone, Sensor, TelemetryPoint
from egorus_monitor.emulator import EmulatorAdapter
from egorus_monitor.influxdb import InfluxManager
from egorus_monitor.persistence import AsyncPersistenceWorker


class MonitorController:
    def __init__(self, enable_persistence: bool = True) -> None:
        self.adapter = EmulatorAdapter()
        self.analyzer = PredictiveAnalyzer()
        self.influx = InfluxManager()
        self.persistence_worker = AsyncPersistenceWorker(self.influx)
        self.persistence_enabled = enable_persistence
        self.states: dict[str, EquipmentState] = {}
        self.events: deque[FaultEvent] = deque(maxlen=250)
        self.running = False
        self._last_zone: dict[str, RiskZone] = {}
        self._seed_states()

    def start(self) -> None:
        self.running = True
        self.adapter.start()

    def stop(self) -> None:
        self.running = False
        self.adapter.stop()

    def start_influx(self) -> bool:
        ok = self.influx.start()
        self.persistence_enabled = ok
        if ok:
            self.persistence_worker.start()
        return ok

    def stop_influx(self) -> None:
        self.persistence_worker.stop()
        self.influx.stop()
        self.persistence_enabled = False

    def tick(self) -> tuple[list[TelemetryPoint], list[HealthSnapshot], list[FaultEvent]]:
        if not self.running:
            return [], [], []
        points = self.adapter.read()
        snapshots: list[HealthSnapshot] = []
        events: list[FaultEvent] = []
        for point in points:
            health = self.analyzer.process(point)
            snapshots.append(health)
            state = self.states.get(point.equipment_id)
            if state is None:
                continue
            state.telemetry = point
            state.health = health
            state.telemetry_history.append(point)
            state.history.append(health)
            state.telemetry_history = state.telemetry_history[-600:]
            state.history = state.history[-600:]
            event = self._maybe_event(point, health)
            if event:
                self.events.appendleft(event)
                events.append(event)

        if self.persistence_enabled:
            self.persistence_worker.enqueue(points, snapshots, events)
        return points, snapshots, events

    def set_fault(self, equipment_id: str, fault_type: FaultType) -> None:
        self.adapter.set_fault(equipment_id, fault_type)

    def add_fault(self, equipment_id: str, fault_type: FaultType) -> None:
        self.adapter.add_fault(equipment_id, fault_type)

    def remove_fault(self, equipment_id: str, fault_type: FaultType) -> None:
        self.adapter.remove_fault(equipment_id, fault_type)

    def clear_faults(self, equipment_id: str) -> None:
        self.adapter.clear_faults(equipment_id)

    def active_faults(self, equipment_id: str) -> list[tuple[FaultType, float]]:
        return self.adapter.active_faults(equipment_id)

    def add_demo_equipment(self, name: str, location: str, rpm: float, power_kw: float) -> EquipmentState:
        safe_id = f"eq-custom-{len(self.states) + 1:02d}"
        equipment = Equipment(safe_id, name or f"Агрегат {len(self.states) + 1}", "Электродвигатель", location or "Не указан", rpm, power_kw)
        sensor = Sensor(f"s-{safe_id}", safe_id, "СВЧ-радар 24 ГГц", "microwave-radar", "корпус / подшипник", "emulator")
        state = EquipmentState(equipment, sensor)
        self.states[equipment.id] = state
        self.adapter._profiles.append(  # controlled extension point for the in-app editor
            self.adapter._profiles[0].__class__(
                equipment=equipment,
                sensor=sensor,
                base_vibration=1.25,
                base_temp=43.0,
                base_rpm=rpm,
                drift=0.001,
            )
        )
        return state

    def _seed_states(self) -> None:
        sensors = {sensor.equipment_id: sensor for sensor in self.adapter.sensors}
        for equipment in self.adapter.equipments:
            sensor = sensors[equipment.id]
            self.states[equipment.id] = EquipmentState(equipment, sensor)

    def _maybe_event(self, point: TelemetryPoint, health: HealthSnapshot) -> FaultEvent | None:
        previous = self._last_zone.get(point.equipment_id)
        self._last_zone[point.equipment_id] = health.risk_zone
        if health.risk_zone is RiskZone.A:
            return None
        if previous == health.risk_zone and health.risk_zone is RiskZone.B:
            return None
        title = f"Зона {health.risk_zone.value}: {health.risk_zone.title}"
        details = f"{health.diagnosis}. HI={health.hi:.2f}, RUL={format_rul(health.rul_hours)}"
        return FaultEvent(
            timestamp=datetime.now(timezone.utc),
            equipment_id=point.equipment_id,
            sensor_id=point.sensor_id,
            risk_zone=health.risk_zone,
            fault_type=point.fault_type,
            title=title,
            details=details,
        )


def format_rul(hours: float) -> str:
    if hours >= 9990:
        return "> 1 года"
    if hours <= 0:
        return "0 ч"
    if hours < 48:
        return f"{hours:.1f} ч"
    return f"{hours / 24:.1f} сут"
