from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

from egorus_monitor.domain import Equipment, FaultType, Sensor, SpectrumSnapshot, TelemetryPoint


@dataclass(slots=True)
class _DeviceProfile:
    equipment: Equipment
    sensor: Sensor
    base_vibration: float
    base_temp: float
    base_rpm: float
    drift: float
    faults: dict[FaultType, float] = field(default_factory=dict)


class EmulatorAdapter:
    name = "Эмулятор СВЧ-датчиков"

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self._running = False
        self._tick = 0
        self._profiles = self._create_profiles()

    @property
    def equipments(self) -> list[Equipment]:
        return [profile.equipment for profile in self._profiles]

    @property
    def sensors(self) -> list[Sensor]:
        return [profile.sensor for profile in self._profiles]

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def set_fault(self, equipment_id: str, fault_type: FaultType) -> None:
        self.clear_faults(equipment_id)
        if fault_type is not FaultType.NONE:
            self.add_fault(equipment_id, fault_type)

    def add_fault(self, equipment_id: str, fault_type: FaultType) -> None:
        if fault_type is FaultType.NONE:
            self.clear_faults(equipment_id)
            return
        for profile in self._profiles:
            if profile.equipment.id == equipment_id:
                profile.faults.setdefault(fault_type, float(self._tick))
                return

    def remove_fault(self, equipment_id: str, fault_type: FaultType) -> None:
        for profile in self._profiles:
            if profile.equipment.id == equipment_id:
                profile.faults.pop(fault_type, None)
                return

    def clear_faults(self, equipment_id: str) -> None:
        for profile in self._profiles:
            if profile.equipment.id == equipment_id:
                profile.faults.clear()
                return

    def active_faults(self, equipment_id: str) -> list[tuple[FaultType, float]]:
        for profile in self._profiles:
            if profile.equipment.id == equipment_id:
                return sorted(((fault, self._fault_severity(profile, fault)) for fault in profile.faults), key=lambda item: item[0].label)
        return []

    def read(self) -> list[TelemetryPoint]:
        if not self._running:
            return []
        self._tick += 1
        now = datetime.now(timezone.utc)
        return [self._generate(profile, now) for profile in self._profiles if profile.sensor.enabled]

    def _create_profiles(self) -> list[_DeviceProfile]:
        equipment = [
            Equipment("eq-press-01", "Прессовый двигатель P-01", "Электродвигатель", "Цех 1 / линия прессов", 3000, 75),
            Equipment("eq-pump-02", "Насосный агрегат N-02", "Насосная группа", "Цех 2 / охлаждение", 1500, 45),
            Equipment("eq-fan-03", "Вентилятор вытяжки V-03", "Вентилятор", "Цех 3 / вентиляция", 3000, 30),
            Equipment("eq-cnc-04", "Шпиндель ЧПУ S-04", "Станок ЧПУ", "Участок мехобработки", 6000, 22),
        ]
        return [
            _DeviceProfile(e, Sensor(f"s-{e.id}", e.id, "СВЧ-радар 24 ГГц", "microwave-radar", "корпус / приводной подшипник", "emulator", 4096), 1.15 + i * 0.2, 42 + i * 2, e.rated_rpm, 0.0007 + i * 0.0002)
            for i, e in enumerate(equipment)
        ]

    def _generate(self, profile: _DeviceProfile, now: datetime) -> TelemetryPoint:
        wobble = math.sin(self._tick / 9 + len(profile.equipment.id)) * 0.08
        noise = self._rng.uniform(-0.08, 0.08)
        natural_drift = min(0.65, self._tick * profile.drift)

        one_x = 0.45 + self._rng.uniform(0.0, 0.12)
        two_x = 0.25 + self._rng.uniform(0.0, 0.08)
        bearing = 0.28 + self._rng.uniform(0.0, 0.12)
        broadband = 0.32 + self._rng.uniform(0.0, 0.10)
        crest = 2.45 + self._rng.uniform(-0.12, 0.18)
        temp_extra = 0.0
        packet_loss = 0.0
        signal_quality = 0.93 + self._rng.uniform(-0.04, 0.03)

        severities = {fault: self._fault_severity(profile, fault) for fault in profile.faults}
        for fault, severity in severities.items():
            if fault is FaultType.BEARING:
                bearing += 2.6 * severity
                crest += 3.8 * severity
                temp_extra += 5.0 * severity
            elif fault is FaultType.IMBALANCE:
                one_x += 3.3 * severity
                crest += 0.7 * severity
                temp_extra += 2.0 * severity
            elif fault is FaultType.MISALIGNMENT:
                two_x += 3.0 * severity
                one_x += 0.8 * severity
                crest += 0.9 * severity
                temp_extra += 4.0 * severity
            elif fault is FaultType.LOOSENESS:
                broadband += 2.7 * severity
                one_x += 1.1 * severity
                two_x += 1.1 * severity
                crest += 1.2 * severity
                temp_extra += 3.0 * severity
            elif fault is FaultType.NOISE_LOSS:
                broadband += 1.0 * severity
                signal_quality -= 0.48 * severity
                packet_loss = max(packet_loss, min(0.32, 0.05 + 0.28 * severity + self._rng.uniform(0.0, 0.04)))

        vibration = profile.base_vibration + natural_drift + wobble + noise + 0.42 * one_x + 0.35 * two_x + 0.28 * bearing + 0.24 * broadband
        vibration = max(0.2, vibration)
        rpm = profile.base_rpm * (1 + self._rng.uniform(-0.008, 0.008))
        temperature = profile.base_temp + temp_extra + vibration * 1.7 + self._rng.uniform(-0.7, 0.8)
        a_spec = max(one_x, two_x, bearing, broadband)
        spectrum = self._spectrum(rpm, one_x, two_x, bearing, broadband)
        primary_fault = self._primary_fault(severities)
        scenario = " + ".join(fault.label for fault in severities) if severities else FaultType.NONE.label

        return TelemetryPoint(
            timestamp=now,
            equipment_id=profile.equipment.id,
            sensor_id=profile.sensor.id,
            source="emulator",
            scenario=scenario,
            fault_type=primary_fault,
            vibration_rms=vibration,
            crest_factor=max(1.2, crest),
            a_spec=a_spec,
            rpm=rpm,
            temperature=temperature,
            one_x_amplitude=one_x,
            two_x_amplitude=two_x,
            bearing_band_energy=bearing,
            broadband_energy=broadband,
            signal_quality=max(0.1, min(1.0, signal_quality)),
            packet_loss=packet_loss,
            spectrum=spectrum,
        )

    def _fault_severity(self, profile: _DeviceProfile, fault_type: FaultType) -> float:
        started_at = profile.faults.get(fault_type)
        if started_at is None:
            return 0.0
        elapsed = max(0.0, self._tick - started_at)
        return min(1.0, 0.18 + elapsed / 85)

    def _primary_fault(self, severities: dict[FaultType, float]) -> FaultType:
        if not severities:
            return FaultType.NONE
        return max(severities.items(), key=lambda item: item[1])[0]

    def _spectrum(self, rpm: float, one_x: float, two_x: float, bearing: float, broadband: float) -> SpectrumSnapshot:
        base = rpm / 60
        frequencies = [i * 2.5 for i in range(121)]
        amplitudes: list[float] = []
        for f in frequencies:
            amp = 0.04 + self._rng.uniform(0.0, 0.035) + broadband * 0.018
            amp += _gaussian(f, base, 2.7) * one_x
            amp += _gaussian(f, base * 2, 3.6) * two_x
            amp += _gaussian(f, base * 4.2, 8.0) * bearing
            amp += _gaussian(f, 180, 55.0) * broadband * 0.42
            amplitudes.append(amp)
        return SpectrumSnapshot(frequencies, amplitudes)


def _gaussian(x: float, center: float, width: float) -> float:
    if width <= 0:
        return 0.0
    return math.exp(-((x - center) ** 2) / (2 * width**2))
