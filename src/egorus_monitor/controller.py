from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from egorus_monitor.analytics import PredictiveAnalyzer
from egorus_monitor.domain import Equipment, EquipmentState, FaultEvent, FaultType, HealthSnapshot, RiskZone, Sensor, TelemetryPoint
from egorus_monitor.emulator import EmulatorAdapter
from egorus_monitor.influxdb import InfluxManager
from egorus_monitor.persistence import AsyncPersistenceWorker
from egorus_monitor.mqtt_client import MqttAdapter


class MonitorController:
    def __init__(self, enable_persistence: bool = True) -> None:
        self.adapter = EmulatorAdapter()
        self.mqtt = MqttAdapter()
        self.analyzer = PredictiveAnalyzer()
        self.influx = InfluxManager()
        self.persistence_worker = AsyncPersistenceWorker(self.influx)
        self.persistence_enabled = enable_persistence
        self.states: dict[str, EquipmentState] = {}
        self.events: deque[FaultEvent] = deque(maxlen=250)
        self.running = False
        self._last_zone: dict[str, RiskZone] = {}
        self.discovered_devices: dict[str, TelemetryPoint] = {}
        self._seed_states()
        

    def start(self) -> None:
        self.running = True
        self.adapter.start()

    def stop(self) -> None:
        self.running = False
        self.adapter.stop()
        self.mqtt.stop()

    def start_mqtt_scan(self) -> None:
        """Запуск сканирования сети"""
        self.discovered_devices.clear()
        self.mqtt.start()

    def stop_mqtt_scan(self) -> None:
        """Остановка сканирования"""
        self.mqtt.stop()

    def start_influx(self) -> bool:
        ok = self.influx.start()
        self.persistence_enabled = ok
        if ok:
            self.persistence_worker.start()
            self._load_history() # Обязательно вызываем функцию здесь!
        return ok

    def _load_history(self) -> None:
        """Подгружает полную историю (телеметрию и здоровье) из БД"""
        print("Запрашиваем историю из базы данных...")
        for eq_id, state in self.states.items():
            # Запрашиваем данные за неделю (10080 минут) при старте
            data = self.influx.read_history(eq_id, minutes_back=10080)
            
            if not isinstance(data, dict):
                continue

            # Очищаем стартовый буфер эмулятора, чтобы насадить чистую историю из БД
            state.telemetry_history.clear()
            state.history.clear()

            # Загружаем телеметрию
            for row in data.get("telemetry", []):
                try:
                    ts = datetime.fromisoformat(row['_time'].replace('Z', '+00:00'))
                    pt = TelemetryPoint(
                        timestamp=ts, equipment_id=eq_id, sensor_id=state.sensor.id,
                        source="db", scenario="Восстановление", fault_type=FaultType.NONE,
                        vibration_rms=float(row.get('vibration_rms', 0)),
                        crest_factor=float(row.get('crest_factor', 0)),
                        a_spec=float(row.get('a_spec', 0)),
                        rpm=float(row.get('rpm', 0)),
                        temperature=float(row.get('temperature', 0)),
                        one_x_amplitude=float(row.get('one_x_amplitude', 0)),
                        two_x_amplitude=float(row.get('two_x_amplitude', 0)),
                        bearing_band_energy=float(row.get('bearing_band_energy', 0)),
                        broadband_energy=float(row.get('broadband_energy', 0)),
                        signal_quality=float(row.get('signal_quality', 1.0)),
                        packet_loss=float(row.get('packet_loss', 0.0))
                    )
                    state.telemetry_history.append(pt)
                except Exception:
                    pass

            # Загружаем здоровье
            for row in data.get("health", []):
                try:
                    ts = datetime.fromisoformat(row['_time'].replace('Z', '+00:00'))
                    snap = HealthSnapshot(
                        timestamp=ts, equipment_id=eq_id, sensor_id=state.sensor.id,
                        source="db", scenario="Восстановление", fault_type=FaultType.NONE,
                        hi=float(row.get('hi', 0)), 
                        rul_hours=float(row.get('rul_hours', 9999.0)), 
                        risk_zone=RiskZone(row.get('risk_zone', 'A')),
                        confidence=float(row.get('confidence', 0.0)), 
                        diagnosis="Загружено", 
                        trend_per_hour=0.0, recommendation=""
                    )
                    state.history.append(snap)
                except Exception:
                    pass
            
            # Ограничиваем буфер под графики
            state.telemetry_history = state.telemetry_history[-600:]
            state.history = state.history[-600:]

            # ПЕРЕДАЕМ ПАМЯТЬ АНАЛИЗАТОРУ: чтобы сглаживание начиналось с последней точки из БД
            if state.history:
                last = state.history[-1]
                self.analyzer._smoothed_hi[eq_id] = last.hi
                self.analyzer._smoothed_rul[eq_id] = last.rul_hours
            
            print(f"Агрегат {eq_id}: УСПЕШНО восстановлено {len(state.history)} точек из реальной БД.")

    def stop_influx(self) -> None:
        self.persistence_worker.stop()
        self.influx.stop()
        self.persistence_enabled = False

    def tick(self) -> tuple[list[TelemetryPoint], list[HealthSnapshot], list[FaultEvent]]:
        if not self.running:
            return [], [], []
            
        mqtt_points = self.mqtt.read()
        known_mqtt = []
        for pt in mqtt_points:
            if pt.equipment_id not in self.states:
                # Если датчика нет в системе - откладываем его в буфер для UI-сканера
                self.discovered_devices[pt.equipment_id] = pt
            else:
                # Если датчик уже добавлен - пускаем его на графики
                known_mqtt.append(pt)

        points = self.adapter.read() + known_mqtt
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
