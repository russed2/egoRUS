import json
import queue
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

from egorus_monitor.domain import TelemetryPoint, FaultType

class MqttAdapter:
    def __init__(self, host="127.0.0.1", port=1883) -> None:
        self.host = host
        self.port = port
        # Подключаем современную реализацию протокола MQTT
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.points_queue = queue.Queue()
        self.connected = False

    def start(self) -> None:
        """Открываем сетевое соединение с брокером"""
        try:
            self.client.connect(self.host, self.port, 60)
            self.client.loop_start() # Запускаем фоновый поток прослушки сети
        except Exception as e:
            print(f"Сетевая ошибка MQTT: {e}")

    def stop(self) -> None:
        """Закрываем порты при выходе"""
        self.client.loop_stop()
        self.client.disconnect()
        self.connected = False

    def read(self) -> list[TelemetryPoint]:
        """Контроллер будет забирать отсюда накопившиеся из сети точки"""
        points = []
        while not self.points_queue.empty():
            try:
                points.append(self.points_queue.get_nowait())
            except queue.Empty:
                break
        return points

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code == 0:
            self.connected = True
            # Подписываемся на ВСЕ аппаратные датчики в цеху по маске (wildcard)
            self.client.subscribe("equipment/+/telemetry")
        else:
            self.connected = False

    def _on_message(self, client, userdata, msg) -> None:
        """Срабатывает мгновенно при прилете пакета из сети (от реального железа)"""
        try:
            # Декодируем байты в JSON
            payload = json.loads(msg.payload.decode('utf-8'))
            
            # Извлекаем ID физического агрегата из топика (например, equipment/eq-cnc-04/telemetry)
            eq_id = msg.topic.split('/')[1]
            
            # Формируем точку данных для нашей математики
            pt = TelemetryPoint(
                timestamp=datetime.now(timezone.utc),
                equipment_id=eq_id,
                sensor_id=payload.get("sensor_id", f"hardware-sensor-{eq_id}"),
                source="mqtt_network", # Явно указываем, что это пришло по сети
                scenario="Аппаратные данные",
                fault_type=FaultType.NONE,
                vibration_rms=float(payload.get("vibration_rms", 0)),
                crest_factor=float(payload.get("crest_factor", 1.0)),
                a_spec=float(payload.get("a_spec", 0)),
                rpm=float(payload.get("rpm", 3000)),
                temperature=float(payload.get("temperature", 20)),
                one_x_amplitude=float(payload.get("one_x_amplitude", 0)),
                two_x_amplitude=float(payload.get("two_x_amplitude", 0)),
                bearing_band_energy=float(payload.get("bearing_band_energy", 0)),
                broadband_energy=float(payload.get("broadband_energy", 0)),
                signal_quality=float(payload.get("signal_quality", 1.0)),
                packet_loss=float(payload.get("packet_loss", 0.0))
            )
            self.points_queue.put(pt)
        except Exception as e:
            print(f"Отброшен битый сетевой пакет: {e}")