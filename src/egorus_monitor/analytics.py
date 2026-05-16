from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from egorus_monitor.domain import FaultType, HealthSnapshot, RiskZone, TelemetryPoint


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def normalize(value: float, baseline: float, limit: float) -> float:
    if limit <= baseline:
        return 0.0
    return clamp((value - baseline) / (limit - baseline))


def risk_zone(hi: float) -> RiskZone:
    if hi <= 0.3:
        return RiskZone.A
    if hi <= 0.6:
        return RiskZone.B
    if hi <= 0.85:
        return RiskZone.C
    return RiskZone.D


def zone_recommendation(zone: RiskZone, diagnosis: str) -> str:
    if zone is RiskZone.A:
        return "Продолжать штатный мониторинг."
    if zone is RiskZone.B:
        return f"Увеличить частоту контроля: {diagnosis.lower()}."
    if zone is RiskZone.C:
        return f"Запланировать диагностику и ремонтный слот: {diagnosis.lower()}."
    return f"Остановить агрегат при первой возможности: {diagnosis.lower()}."


@dataclass(slots=True)
class AnalyticsConfig:
    vibration_baseline: float = 1.2
    vibration_limit: float = 7.1
    crest_baseline: float = 2.4
    crest_limit: float = 7.0
    spectral_baseline: float = 0.3
    spectral_limit: float = 4.5
    hi_critical: float = 0.85
    max_rul_hours: float = 9999.0
    demo_hours_per_sample: float = 8.0
    trend_window: int = 36
    min_trend_samples: int = 14


class PredictiveAnalyzer:
    def __init__(self, config: AnalyticsConfig | None = None) -> None:
        self.config = config or AnalyticsConfig()
        self._hi_history: dict[str, deque[tuple[datetime, float]]] = defaultdict(lambda: deque(maxlen=720))
        self._smoothed_hi: dict[str, float] = {}
        self._smoothed_trend: dict[str, float] = {}
        self._smoothed_rul: dict[str, float] = {}

    def process(self, point: TelemetryPoint) -> HealthSnapshot:
        vrms_n = normalize(point.vibration_rms, self.config.vibration_baseline, self.config.vibration_limit)
        crest_n = normalize(point.crest_factor, self.config.crest_baseline, self.config.crest_limit)
        spectral_n = normalize(point.a_spec, self.config.spectral_baseline, self.config.spectral_limit)
        hi_raw = clamp(0.4 * vrms_n + 0.3 * crest_n + 0.3 * spectral_n)

        prev = self._smoothed_hi.get(point.equipment_id, hi_raw)
        hi = 0.72 * prev + 0.28 * hi_raw
        self._smoothed_hi[point.equipment_id] = hi

        history = self._hi_history[point.equipment_id]
        history.append((point.timestamp, hi))
        trend = self._estimate_trend_per_hour(history)
        trend = self._smooth_trend(point.equipment_id, trend)
        rul = self._estimate_rul_hours(point.equipment_id, hi, trend, len(history))
        zone = risk_zone(hi)
        diagnosis = classify_fault(point)
        confidence = estimate_confidence(point, hi, trend)

        return HealthSnapshot(
            timestamp=point.timestamp,
            equipment_id=point.equipment_id,
            sensor_id=point.sensor_id,
            source=point.source,
            scenario=point.scenario,
            fault_type=point.fault_type,
            hi=hi,
            rul_hours=rul,
            risk_zone=zone,
            confidence=confidence,
            diagnosis=diagnosis,
            trend_per_hour=trend,
            recommendation=zone_recommendation(zone, diagnosis),
        )

    def _estimate_trend_per_hour(self, history: deque[tuple[datetime, float]]) -> float:
        if len(history) < self.config.min_trend_samples:
            return 0.0

        recent = list(history)[-self.config.trend_window :]
        xs = [i * self.config.demo_hours_per_sample for i in range(len(recent))]
        ys = [hi for _, hi in recent]
        x_mean = sum(xs) / len(xs)
        y_mean = sum(ys) / len(ys)
        denom = sum((x - x_mean) ** 2 for x in xs)
        if denom <= 1e-9:
            return 0.0
        slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True)) / denom
        return max(0.0, slope)

    def _smooth_trend(self, equipment_id: str, trend_per_hour: float) -> float:
        previous = self._smoothed_trend.get(equipment_id, trend_per_hour)
        smoothed = 0.85 * previous + 0.15 * trend_per_hour
        self._smoothed_trend[equipment_id] = smoothed
        return smoothed

    def _estimate_rul_hours(self, equipment_id: str, hi: float, trend_per_hour: float, sample_count: int) -> float:
        if hi >= self.config.hi_critical:
            candidate = 0.0
        else:
            candidate = self._severity_rul_hours(hi)
            if hi <= 0.30:
                candidate = self.config.max_rul_hours
            elif sample_count >= self.config.min_trend_samples and trend_per_hour > 0.00002:
                trend_rul = (self.config.hi_critical - hi) / trend_per_hour
                zone_floor = self._zone_rul_floor(hi)
                candidate = min(candidate, max(zone_floor, trend_rul))

        candidate = candidate if isfinite(candidate) else self.config.max_rul_hours
        previous = self._smoothed_rul.get(equipment_id)
        if previous is None:
            self._smoothed_rul[equipment_id] = candidate
            return candidate

        if candidate < previous:
            max_drop = max(36.0, previous * 0.22)
            value = max(candidate, previous - max_drop)
        else:
            max_rise = max(24.0, previous * 0.08)
            value = min(candidate, previous + max_rise)

        self._smoothed_rul[equipment_id] = clamp(value, 0.0, self.config.max_rul_hours)
        return self._smoothed_rul[equipment_id]

    def _severity_rul_hours(self, hi: float) -> float:
        if hi <= 0.30:
            return self.config.max_rul_hours
        if hi <= 0.60:
            return _lerp(4320.0, 720.0, (hi - 0.30) / 0.30)
        if hi <= 0.85:
            return _lerp(720.0, 72.0, (hi - 0.60) / 0.25)
        return _lerp(72.0, 0.0, (hi - 0.85) / 0.15)

    def _zone_rul_floor(self, hi: float) -> float:
        if hi <= 0.30:
            return 2160.0
        if hi <= 0.60:
            return 480.0
        if hi <= 0.85:
            return 48.0
        return 0.0


def _lerp(start: float, end: float, factor: float) -> float:
    factor = clamp(factor)
    return start + (end - start) * factor


def classify_fault(point: TelemetryPoint) -> str:
    if point.packet_loss > 0.12 or point.signal_quality < 0.55:
        return "Нестабильный канал связи или сильные СВЧ-помехи"
    if point.bearing_band_energy > max(point.one_x_amplitude, point.two_x_amplitude) * 1.25 and point.crest_factor > 4.2:
        return "Вероятен дефект подшипника качения"
    if point.one_x_amplitude > point.two_x_amplitude * 1.45 and point.one_x_amplitude > 1.15:
        return "Вероятен дисбаланс ротора на 1x оборотной частоте"
    if point.two_x_amplitude > point.one_x_amplitude * 1.15 and point.two_x_amplitude > 1.05:
        return "Вероятна расцентровка муфты или вала"
    if point.broadband_energy > 1.4 and point.crest_factor < 4.5:
        return "Вероятно ослабление крепления или рост широкополосной вибрации"
    if point.fault_type is not FaultType.NONE:
        return point.fault_type.label
    return "Отклонений по спектральным признакам не выявлено"


def estimate_confidence(point: TelemetryPoint, hi: float, trend_per_hour: float) -> float:
    signal_part = clamp(point.signal_quality)
    history_part = clamp(0.55 + min(trend_per_hour * 4.0, 0.25))
    severity_part = clamp(0.55 + hi * 0.35)
    return clamp(0.45 * signal_part + 0.25 * history_part + 0.30 * severity_part)
