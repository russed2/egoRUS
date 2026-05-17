from __future__ import annotations

import threading
import pyqtgraph as real_pg
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from egorus_monitor.controller import MonitorController, format_rul
from egorus_monitor.domain import EquipmentState, FaultType, RiskZone
from egorus_monitor.ui import simple_charts as pg
from egorus_monitor.ui.style import APP_STYLESHEET


EMULATOR_CHART_OPTIONS = [
    ("vibration", "V RMS · виброскорость"),
    ("hi", "Health Index"),
    ("temperature", "Температура"),
    ("crest", "Пик-фактор"),
    ("spectrum", "Спектр вибрации"),
    ("components", "Признаки узлов"),
    ("quality", "Качество канала"),
    ("rpm", "Обороты"),
    ("rul", "Остаточный ресурс"),
]


class MainWindow(QMainWindow):
    def __init__(self, start_influx: bool = True) -> None:
        super().__init__()
        self.controller = MonitorController(enable_persistence=start_influx)
        self.controller.start()
        if start_influx:
            self.controller.start_influx()

        pg.setConfigOptions(antialias=True, background="#121b23", foreground="#cbd8de")
        self.setWindowTitle("EgoRUS Predictive Monitor")
        self.setStyleSheet(APP_STYLESHEET)
        self._ui_tick_counter = 0
        self._influx_starting = False

        self.nav_buttons: list[QPushButton] = []
        self.pages = QStackedWidget()
        self._build_shell()
        self._build_pages()
        self._refresh_static_data()
        if start_influx:
            self._start_influx_async()

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.controller.stop_influx()
        super().closeEvent(event)

    def _build_shell(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(242)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 18, 18, 18)
        side.setSpacing(10)

        title = QLabel("EgoRUS")
        title.setObjectName("AppTitle")
        subtitle = QLabel("Predictive Monitor")
        subtitle.setObjectName("AppSubtitle")
        side.addWidget(title)
        side.addWidget(subtitle)
        side.addSpacing(16)

        nav = [
            ("Обзор парка", "Состояние всех агрегатов"),
            ("Реальное время", "Текущие значения и графики"),
            ("История", "Архив телеметрии"),
            ("Предиктивная аналитика", "HI, RUL и дефекты"),
            ("Эмулятор", "Сценарии и поломки"),
            ("Оборудование", "Устройства и датчики"),
            ("Подключения", "InfluxDB и адаптеры"),
        ]
        for index, (label, tooltip) in enumerate(nav):
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setToolTip(tooltip)
            button.clicked.connect(lambda checked=False, i=index: self._select_page(i))
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch(1)

        self.connection_badge = QLabel("InfluxDB: проверка")
        self.connection_badge.setObjectName("Muted")
        side.addWidget(self.connection_badge)
        self.runtime_badge = QLabel("Эмулятор: запущен")
        self.runtime_badge.setObjectName("Muted")
        side.addWidget(self.runtime_badge)

        layout.addWidget(sidebar)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self._select_page(0)

    def _build_pages(self) -> None:
        self.pages.addWidget(self._dashboard_page())
        self.pages.addWidget(self._realtime_page())
        self.pages.addWidget(self._history_page())
        self.pages.addWidget(self._analytics_page())
        self.pages.addWidget(self._emulator_page())
        self.pages.addWidget(self._equipment_page())
        self.pages.addWidget(self._connections_page())

    def _select_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        if hasattr(self, "fleet_table"):
            self._update_current_page()

    def _dashboard_page(self) -> QWidget:
        page = self._page("Обзор парка оборудования")
        layout = page.layout()
        assert isinstance(layout, QVBoxLayout)

        kpis = QHBoxLayout()
        self.kpi_total, self.kpi_total_value = self._kpi_card("Устройств", "0", "под наблюдением", "KpiOk")
        self.kpi_normal, self.kpi_normal_value = self._kpi_card("Зона A", "0", "штатная работа", "KpiOk")
        self.kpi_warning, self.kpi_warning_value = self._kpi_card("Зоны B/C", "0", "требуют внимания", "KpiWarn")
        self.kpi_critical, self.kpi_critical_value = self._kpi_card("Зона D", "0", "критический риск", "KpiCrit")
        for card in [self.kpi_total, self.kpi_normal, self.kpi_warning, self.kpi_critical]:
            kpis.addWidget(card)
        layout.addLayout(kpis)

        middle = QHBoxLayout()
        fleet_panel = self._panel("Парк агрегатов")
        fleet_layout = fleet_panel.layout()
        assert isinstance(fleet_layout, QVBoxLayout)
        self.fleet_table = self._table(["Агрегат", "Локация", "HI", "Зона", "RUL", "Диагноз"])
        fleet_layout.addWidget(self.fleet_table)
        middle.addWidget(fleet_panel, 3)

        events_panel = self._panel("Журнал тревог")
        events_layout = events_panel.layout()
        assert isinstance(events_layout, QVBoxLayout)
        self.event_table = self._table(["Время", "Агрегат", "Событие", "Описание"])
        events_layout.addWidget(self.event_table)
        middle.addWidget(events_panel, 2)
        layout.addLayout(middle, 1)

        self.overview_plot = pg.PlotWidget()
        self._style_plot(self.overview_plot, "Тренд индекса состояния HI", "сек", "HI")
        layout.addWidget(self.overview_plot, 1)
        return page

    def _realtime_page(self) -> QWidget:
        page = self._page("Мониторинг в реальном времени")
        layout = page.layout()
        assert isinstance(layout, QVBoxLayout)

        top = QHBoxLayout()
        self.real_device_combo = QComboBox()
        self.real_device_combo.currentIndexChanged.connect(lambda _=0: self._update_all_views())
        top.addWidget(QLabel("Агрегат"))
        top.addWidget(self.real_device_combo, 1)
        self.start_button = QPushButton("Пауза")
        self.start_button.clicked.connect(self._toggle_runtime)
        top.addWidget(self.start_button)
        layout.addLayout(top)

        metrics = QGridLayout()
        self.metric_labels: dict[str, QLabel] = {}
        for i, (key, label, unit) in enumerate(
            [
                ("vibration", "V RMS", "мм/с"),
                ("crest", "Пик-фактор", ""),
                ("rpm", "Обороты", "об/мин"),
                ("temp", "Температура", "°C"),
                ("quality", "Канал", "%"),
                ("loss", "Потери", "%"),
            ]
        ):
            card, value_label = self._metric_card(label, unit)
            self.metric_labels[key] = value_label
            metrics.addWidget(card, i // 3, i % 3)
        layout.addLayout(metrics)

        plots = QHBoxLayout()
        self.real_plot = pg.PlotWidget()
        self._style_plot(self.real_plot, "Последняя минута: виброскорость", "сек", "мм/с")
        plots.addWidget(self.real_plot, 3)
        self.spectrum_plot = pg.PlotWidget()
        self._style_plot(self.spectrum_plot, "Спектр вибрации", "Гц", "ампл.")
        plots.addWidget(self.spectrum_plot, 2)
        layout.addLayout(plots, 1)
        return page

    def _history_page(self) -> QWidget:
        page = self._page("История и архив показателей")
        layout = page.layout()
        assert isinstance(layout, QVBoxLayout)

        controls = QHBoxLayout()
        self.history_device_combo = QComboBox()
        self.history_device_combo.currentIndexChanged.connect(lambda _=0: self._update_history())
        
        self.history_metric_combo = QComboBox()
        self.history_metric_combo.addItems(["V RMS", "Health Index", "Температура", "Пик-фактор"])
        self.history_metric_combo.currentIndexChanged.connect(lambda _=0: self._update_history())
        
        # --- ТВОЙ ПОЛНЫЙ СПИСОК ПЕРИОДОВ (В МИНУТАХ) ---
        self.history_period_combo = QComboBox()
        self.history_period_combo.addItem("За последние 30 минут", 30)
        self.history_period_combo.addItem("За последний час", 60)
        self.history_period_combo.addItem("За последние 3 часа", 180)
        self.history_period_combo.addItem("За последние 6 часов", 360)
        self.history_period_combo.addItem("За последние 12 часов", 720)
        self.history_period_combo.addItem("За последние 24 часа", 1440)
        self.history_period_combo.addItem("За последние 3 дня", 4320)
        self.history_period_combo.addItem("За последние 7 дней", 10080)
        self.history_period_combo.addItem("За последние 14 дней", 20160)
        self.history_period_combo.addItem("За последний месяц", 43200)
        self.history_period_combo.addItem("За последние 3 месяца", 129600)
        self.history_period_combo.addItem("За полгода", 259200)
        self.history_period_combo.currentIndexChanged.connect(lambda _=0: self._update_history())

        refresh = QPushButton("Загрузить из БД")
        refresh.setObjectName("AccentButton")
        refresh.clicked.connect(self._update_history)
        
        controls.addWidget(QLabel("Агрегат:"))
        controls.addWidget(self.history_device_combo, 2)
        controls.addWidget(QLabel("Показатель:"))
        controls.addWidget(self.history_metric_combo, 1)
        controls.addWidget(QLabel("Период:"))
        controls.addWidget(self.history_period_combo, 1)
        controls.addWidget(refresh)
        layout.addLayout(controls)

        # Подключаем DateAxisItem, чтобы нижняя ось автоматически подстраивалась под даты
        axis = real_pg.DateAxisItem(orientation='bottom')
        self.history_plot_interactive = real_pg.PlotWidget(axisItems={'bottom': axis})
        self.history_plot_interactive.setBackground("#121b23")
        self.history_plot_interactive.showGrid(x=True, y=True, alpha=0.3)

        # Блокируем стандартное контекстное меню pyqtgraph на ПКМ
        self.history_plot_interactive.getPlotItem().setMenuEnabled(False)
        self.history_plot_interactive.getPlotItem().getViewBox().setMenuEnabled(False)
        
        # Запрещаем графику физически опускаться ниже нуля по Y
        self.history_plot_interactive.getPlotItem().getViewBox().setLimits(yMin=0)
        
        # Привязываем отслеживание кликов мыши НАПРЯМУЮ к контейнеру отображения ViewBox
        self.history_plot_interactive.scene().sigMouseClicked.connect(self._on_history_plot_clicked)
        
        layout.addWidget(self.history_plot_interactive, 1)

        panel = self._panel("Архив InfluxDB")
        panel_layout = panel.layout()
        assert isinstance(panel_layout, QVBoxLayout)
        self.history_status = QLabel("Выберите параметры и нажмите 'Загрузить из БД'.")
        self.history_status.setObjectName("Muted")
        panel_layout.addWidget(self.history_status)
        layout.addWidget(panel)
        return page

    def _analytics_page(self) -> QWidget:
        page = self._page("Предиктивная аналитика")
        layout = page.layout()
        assert isinstance(layout, QVBoxLayout)

        controls = QHBoxLayout()
        self.analytics_device_combo = QComboBox()
        self.analytics_device_combo.currentIndexChanged.connect(lambda _=0: self._update_analytics())
        controls.addWidget(QLabel("Агрегат"))
        controls.addWidget(self.analytics_device_combo, 1)
        layout.addLayout(controls)

        grid = QGridLayout()
        self.hi_value = QLabel("0.00")
        self.hi_value.setObjectName("MetricValue")
        hi_card = self._panel("Health Index")
        hi_layout = hi_card.layout()
        assert isinstance(hi_layout, QVBoxLayout)
        hi_layout.addWidget(self.hi_value)
        self.zone_label = QLabel("Зона A")
        self.zone_label.setObjectName("Muted")
        hi_layout.addWidget(self.zone_label)
        grid.addWidget(hi_card, 0, 0)

        rul_card = self._panel("Остаточный ресурс")
        rul_layout = rul_card.layout()
        assert isinstance(rul_layout, QVBoxLayout)
        self.rul_value = QLabel("> 1 года")
        self.rul_value.setObjectName("MetricValue")
        rul_layout.addWidget(self.rul_value)
        self.confidence_label = QLabel("Достоверность 0%")
        self.confidence_label.setObjectName("Muted")
        rul_layout.addWidget(self.confidence_label)
        grid.addWidget(rul_card, 0, 1)

        diag_card = self._panel("Диагностическое заключение")
        diag_layout = diag_card.layout()
        assert isinstance(diag_layout, QVBoxLayout)
        self.diagnosis_label = QLabel("Ожидание данных")
        self.diagnosis_label.setWordWrap(True)
        self.recommendation_label = QLabel("")
        self.recommendation_label.setObjectName("Muted")
        self.recommendation_label.setWordWrap(True)
        diag_layout.addWidget(self.diagnosis_label)
        diag_layout.addWidget(self.recommendation_label)
        grid.addWidget(diag_card, 0, 2)
        layout.addLayout(grid)

        plots = QHBoxLayout()
        self.forecast_plot = pg.PlotWidget()
        self._style_plot(self.forecast_plot, "Прогноз HI до критического порога", "часы", "HI")
        plots.addWidget(self.forecast_plot, 3)
        self.component_plot = pg.PlotWidget()
        self._style_plot(self.component_plot, "Диагностические признаки узлов", "узел", "ампл.")
        plots.addWidget(self.component_plot, 2)
        layout.addLayout(plots, 1)
        return page

    def _emulator_page(self) -> QWidget:
        page = self._page("Эмулятор дефектов и реакция графиков")
        layout = page.layout()
        assert isinstance(layout, QVBoxLayout)

        top = QHBoxLayout()
        controls = self._panel("Конструктор дефектов")
        controls_layout = controls.layout()
        assert isinstance(controls_layout, QVBoxLayout)

        row = QHBoxLayout()
        self.emulator_device_combo = QComboBox()
        self.emulator_device_combo.currentIndexChanged.connect(lambda _=0: self._update_emulator_page())
        self.defect_combo = QComboBox()
        self._fault_items = [
            ("Износ подшипника", FaultType.BEARING),
            ("Дисбаланс ротора", FaultType.IMBALANCE),
            ("Расцентровка муфты", FaultType.MISALIGNMENT),
            ("Ослабление крепления", FaultType.LOOSENESS),
            ("Помехи / потери связи", FaultType.NOISE_LOSS),
        ]
        for label, fault in self._fault_items:
            self.defect_combo.addItem(label, fault.value)
        self.scenario_combo = self.defect_combo
        add_fault = QPushButton("+ Добавить дефект")
        add_fault.setObjectName("DangerButton")
        add_fault.clicked.connect(self._apply_fault)
        remove_fault = QPushButton("Удалить выбранный")
        remove_fault.clicked.connect(self._remove_selected_fault)
        clear_fault = QPushButton("Сбросить все")
        clear_fault.setObjectName("AccentButton")
        clear_fault.clicked.connect(self._clear_fault)
        row.addWidget(QLabel("Агрегат"))
        row.addWidget(self.emulator_device_combo, 1)
        row.addWidget(QLabel("Дефект"))
        row.addWidget(self.defect_combo, 1)
        row.addWidget(add_fault)
        row.addWidget(remove_fault)
        row.addWidget(clear_fault)
        controls_layout.addLayout(row)
        self.scenario_hint = QLabel("Добавляйте несколько дефектов через плюс: эмулятор суммирует их влияние, а графики сразу показывают изменение вибрации, спектра, HI и канала связи.")
        self.scenario_hint.setObjectName("Muted")
        controls_layout.addWidget(self.scenario_hint)
        self.active_fault_table = self._table(["Активный дефект", "Развитие", "Как проявляется на графиках"])
        controls_layout.addWidget(self.active_fault_table)
        top.addWidget(controls, 3)

        chart_controls = self._panel("Графики в этом окне")
        chart_controls_layout = chart_controls.layout()
        assert isinstance(chart_controls_layout, QVBoxLayout)
        chart_row = QHBoxLayout()
        self.add_chart_combo = QComboBox()
        for key, label in EMULATOR_CHART_OPTIONS:
            self.add_chart_combo.addItem(label, key)
        add_chart = QPushButton("+ Добавить график")
        add_chart.setObjectName("AccentButton")
        add_chart.clicked.connect(lambda _=False: self._add_emulator_chart(str(self.add_chart_combo.currentData())))
        clear_charts = QPushButton("Сбросить набор")
        clear_charts.clicked.connect(self._clear_emulator_charts)
        chart_row.addWidget(self.add_chart_combo, 1)
        chart_row.addWidget(add_chart)
        chart_row.addWidget(clear_charts)
        chart_controls_layout.addLayout(chart_row)
        self.emulator_table = self._table(["Агрегат", "Дефекты", "V RMS", "HI", "Зона", "Канал"])
        chart_controls_layout.addWidget(self.emulator_table)
        top.addWidget(chart_controls, 2)
        layout.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.emulator_charts_host = QWidget()
        self.emulator_charts_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.emulator_charts_grid = QGridLayout(self.emulator_charts_host)
        self.emulator_charts_grid.setContentsMargins(0, 0, 0, 0)
        self.emulator_charts_grid.setSpacing(12)
        self.emulator_chart_cards: list[dict[str, object]] = []
        scroll.setWidget(self.emulator_charts_host)
        layout.addWidget(scroll, 1)
        for metric in ["vibration", "hi", "spectrum", "components"]:
            self._add_emulator_chart(metric)
        return page

    def _equipment_page(self) -> QWidget:
        page = self._page("Оборудование и датчики")
        layout = page.layout()
        assert isinstance(layout, QVBoxLayout)

        tabs = QTabWidget()
        equipment_tab = QWidget()
        equipment_layout = QVBoxLayout(equipment_tab)
        self.equipment_table = self._table(["ID", "Название", "Тип", "Локация", "RPM", "кВт"])
        equipment_layout.addWidget(self.equipment_table)

        add_panel = self._panel("Добавить агрегат в демо-контур")
        add_layout = add_panel.layout()
        assert isinstance(add_layout, QVBoxLayout)
        form = QHBoxLayout()
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("Название агрегата")
        self.new_location = QLineEdit()
        self.new_location.setPlaceholderText("Локация")
        self.new_rpm = QSpinBox()
        self.new_rpm.setRange(300, 12000)
        self.new_rpm.setValue(3000)
        self.new_power = QDoubleSpinBox()
        self.new_power.setRange(0.5, 1000)
        self.new_power.setValue(55)
        self.new_power.setSuffix(" кВт")
        add_button = QPushButton("Добавить")
        add_button.clicked.connect(lambda _=False: self._add_equipment(show_message=True))
        add_layout.addLayout(form)
        equipment_layout.addWidget(add_panel)

        sensor_tab = QWidget()
        sensor_layout = QVBoxLayout(sensor_tab)
        self.sensor_table = self._table(["ID", "Агрегат", "Тип", "Позиция", "Адаптер", "Гц", "Статус"])
        sensor_layout.addWidget(self.sensor_table)
        tabs.addTab(equipment_tab, "Агрегаты")
        tabs.addTab(sensor_tab, "Датчики")
        layout.addWidget(tabs, 1)
        return page

    def _connections_page(self) -> QWidget:
        page = self._page("Подключения и инфраструктура")
        layout = page.layout()
        assert isinstance(layout, QVBoxLayout)

        tabs = QTabWidget()
        influx_tab = QWidget()
        influx_layout = QVBoxLayout(influx_tab)
        self.influx_status = QLabel()
        self.influx_status.setWordWrap(True)
        self.influx_binary = QLabel()
        self.influx_binary.setObjectName("Muted")
        start = QPushButton("Запустить InfluxDB")
        start.clicked.connect(self._start_influx_clicked)
        stop = QPushButton("Остановить InfluxDB")
        stop.clicked.connect(self._stop_influx_clicked)
        row = QHBoxLayout()
        row.addWidget(start)
        row.addWidget(stop)
        row.addStretch(1)
        influx_layout.addWidget(self.influx_status)
        influx_layout.addWidget(self.influx_binary)
        influx_layout.addLayout(row)
        influx_layout.addStretch(1)

        adapters_tab = QWidget()
        adapters_layout = QVBoxLayout(adapters_tab)
        adapter_card = self._panel("Адаптеры источников данных")
        adapter_layout = adapter_card.layout()
        assert isinstance(adapter_layout, QVBoxLayout)
        adapter_layout.addWidget(QLabel("Активен: эмулятор СВЧ-датчиков"))
        self.mqtt_status = QLabel("MQTT Брокер: ожидание подключения (127.0.0.1:1883)")
        self.mqtt_status.setObjectName("Muted")
        self.mqtt_status.setWordWrap(True)
        adapter_layout.addWidget(self.mqtt_status)

        self.scan_button = QPushButton("Сканировать сеть (Поиск новых датчиков)")
        self.scan_button.setObjectName("AccentButton")
        self.scan_button.clicked.connect(self._toggle_mqtt_scan)
        adapter_layout.addWidget(self.scan_button)

        adapters_layout.addWidget(adapter_card)
        adapters_layout.addStretch(1)

        tabs.addTab(influx_tab, "InfluxDB")
        tabs.addTab(adapters_tab, "Адаптеры")
        layout.addWidget(tabs, 1)
        return page

    def _page(self, title: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        label = QLabel(title)
        label.setObjectName("PageTitle")
        layout.addWidget(label)
        return page

    def _panel(self, title: str) -> QFrame:
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        return panel

    def _kpi_card(self, label: str, value: str, hint: str, object_name: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName(object_name)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        caption = QLabel(label)
        caption.setObjectName("MetricLabel")
        metric = QLabel(value)
        metric.setObjectName("MetricValue")
        note = QLabel(hint)
        note.setObjectName("Muted")
        layout.addWidget(caption)
        layout.addWidget(metric)
        layout.addWidget(note)
        return card, metric

    def _metric_card(self, label: str, unit: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        caption = QLabel(label)
        caption.setObjectName("MetricLabel")
        value = QLabel("--")
        value.setObjectName("MetricValue")
        hint = QLabel(unit)
        hint.setObjectName("Muted")
        layout.addWidget(caption)
        layout.addWidget(value)
        layout.addWidget(hint)
        return card, value

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return table

    def _style_plot(self, plot: pg.PlotWidget, title: str, bottom: str, left: str) -> None:
        plot.setTitle(title, color="#f0f6f6", size="12pt")
        plot.showGrid(x=True, y=True, alpha=0.22)
        plot.setLabel("bottom", bottom)
        plot.setLabel("left", left)
        plot.getPlotItem().getAxis("bottom").setPen("#516977")
        plot.getPlotItem().getAxis("left").setPen("#516977")

    def _refresh_static_data(self) -> None:
        combos = [self.real_device_combo, self.history_device_combo, self.analytics_device_combo, self.emulator_device_combo]
        current_ids = {combo.objectName(): combo.currentData() for combo in combos}
        for combo in combos:
            combo.blockSignals(True)
            combo.clear()
            for state in self.controller.states.values():
                combo.addItem(state.equipment.name, state.equipment.id)
            combo.blockSignals(False)
            selected = current_ids.get(combo.objectName())
            if selected:
                index = combo.findData(selected)
                if index >= 0:
                    combo.setCurrentIndex(index)
        self._update_equipment_tables()
        self._update_connection_status()

    def _on_tick(self) -> None:
        self._ui_tick_counter += 1
        self.controller.tick()
        self._update_current_page()
        if self._ui_tick_counter % 5 == 0:
            self._update_connection_status()
            
        # --- ПРОВЕРКА НАЙДЕННЫХ УСТРОЙСТВ ---
        if getattr(self, "_is_scanning_mqtt", False):
            if self.controller.discovered_devices:
                # Берем первый пойманный сигнал
                found_id = list(self.controller.discovered_devices.keys())[0]
                
                self.new_name.setText(found_id) # Вписываем ID в форму
                self._stop_mqtt_scan_ui()       # Закрываем порт
                self.controller.discovered_devices.clear()
                
                # Автоматически переключаем интерфейс на вкладку "Оборудование" (индекс 5)
                self._select_page(5) 
                QMessageBox.information(self, "Устройство найдено!", f"Успешно перехвачен сигнал от внешнего датчика: {found_id}.\nДозаполните параметры (Локация, Мощность) и нажмите кнопку 'Добавить'.")

    def _update_all_views(self) -> None:
        self._update_dashboard()
        self._update_realtime()
        self._update_history()
        self._update_analytics()
        self._update_emulator_page()
        self._update_connection_status()

    def _update_current_page(self) -> None:
        index = self.pages.currentIndex()
        if index == 0:
            self._update_dashboard()
        elif index == 1:
            self._update_realtime()
        elif index == 2:
            pass
        elif index == 3:
            self._update_analytics()
        elif index == 4:
            self._update_emulator_page()
        elif index == 5:
            self._update_equipment_tables()
        elif index == 6:
            self._update_connection_status()

    def _selected_state(self, combo: QComboBox) -> EquipmentState | None:
        equipment_id = combo.currentData()
        if not equipment_id:
            return next(iter(self.controller.states.values()), None)
        return self.controller.states.get(str(equipment_id))

    def _update_dashboard(self) -> None:
        states = list(self.controller.states.values())
        zones = [s.health.risk_zone for s in states if s.health]
        total = len(states)
        normal = sum(1 for z in zones if z is RiskZone.A)
        warning = sum(1 for z in zones if z in (RiskZone.B, RiskZone.C))
        critical = sum(1 for z in zones if z is RiskZone.D)
        self.kpi_total_value.setText(str(total))
        self.kpi_normal_value.setText(str(normal))
        self.kpi_warning_value.setText(str(warning))
        self.kpi_critical_value.setText(str(critical))

        self.fleet_table.setRowCount(len(states))
        for row, state in enumerate(states):
            health = state.health
            values = [
                state.equipment.name,
                state.equipment.location,
                f"{health.hi:.2f}" if health else "--",
                f"{health.risk_zone.value} · {health.risk_zone.title}" if health else "--",
                format_rul(health.rul_hours) if health else "--",
                health.diagnosis if health else "Ожидание данных",
            ]
            self._set_row(self.fleet_table, row, values, health.risk_zone if health else None)

        events = list(self.controller.events)[:80]
        self.event_table.setRowCount(len(events))
        for row, event in enumerate(events):
            local_time = event.timestamp.astimezone().strftime("%H:%M:%S")
            equipment = self.controller.states.get(event.equipment_id)
            values = [local_time, equipment.equipment.name if equipment else event.equipment_id, event.title, event.details]
            self._set_row(self.event_table, row, values, event.risk_zone)

        self.overview_plot.clear()
        colors = ["#20a08f", "#f0c64b", "#ff8a3d", "#7cb6ff", "#d879ff"]
        for index, state in enumerate(states[:5]):
            if not state.history:
                continue
            x = _relative_seconds([h.timestamp for h in state.history[-120:]])
            y = [h.hi for h in state.history[-120:]]
            self.overview_plot.plot(x, y, pen=pg.mkPen(colors[index % len(colors)], width=2), name=state.equipment.name)
        self.overview_plot.addLine(y=0.85, pen=pg.mkPen("#ff4d5f", width=1, style=Qt.PenStyle.DashLine))

    def _update_realtime(self) -> None:
        state = self._selected_state(self.real_device_combo)
        if not state or not state.telemetry:
            return
        point = state.telemetry
        self.metric_labels["vibration"].setText(f"{point.vibration_rms:.2f}")
        self.metric_labels["crest"].setText(f"{point.crest_factor:.2f}")
        self.metric_labels["rpm"].setText(f"{point.rpm:.0f}")
        self.metric_labels["temp"].setText(f"{point.temperature:.1f}")
        self.metric_labels["quality"].setText(f"{point.signal_quality * 100:.0f}")
        self.metric_labels["loss"].setText(f"{point.packet_loss * 100:.1f}")

        points = state.telemetry_history[-60:]
        self.real_plot.clear()
        if points:
            x = _relative_seconds([p.timestamp for p in points])
            self.real_plot.plot(x, [p.vibration_rms for p in points], pen=pg.mkPen("#20a08f", width=3))
            self.real_plot.addLine(y=7.1, pen=pg.mkPen("#ff4d5f", width=1, style=Qt.PenStyle.DashLine))
        self.spectrum_plot.clear()
        if point.spectrum:
            self.spectrum_plot.plot(point.spectrum.frequencies, point.spectrum.amplitudes, pen=pg.mkPen("#f0c64b", width=2))

    def _update_history(self) -> None:
        state = self._selected_state(self.history_device_combo)
        if not state or not hasattr(self, "history_plot_interactive"):
            return
            
        metric = self.history_metric_combo.currentText()
        minutes_back = self.history_period_combo.currentData()
        
        self.history_status.setText(f"Запрос к InfluxDB за последние {minutes_back} мин...")
        self.history_plot_interactive.clear()
        
        # Сбрасываем старые лимиты по оси X перед новой загрузкой, сохраняя пол по Y >= 0
        self.history_plot_interactive.getPlotItem().getViewBox().setLimits(xMin=None, xMax=None, yMin=0)
        
        data = self.controller.influx.read_history(state.equipment.id, minutes_back=int(minutes_back))
        
        if not isinstance(data, dict) or (not data.get("telemetry") and not data.get("health")):
            self.history_status.setText("Данные за выбранный период в базе не найдены.")
            return

        x_coords = []
        y_coords = []

        if metric == "Health Index":
            rows = data.get("health", [])
            for row in rows:
                try:
                    ts = datetime.fromisoformat(row['_time'].replace('Z', '+00:00')).timestamp()
                    x_coords.append(ts)
                    y_coords.append(float(row.get('hi', 0)))
                except: pass
            color = (32, 160, 143)
            if x_coords:
                self.history_plot_interactive.addLine(y=0.85, pen=real_pg.mkPen('#ff4d5f', width=1, style=Qt.PenStyle.DashLine))
        else:
            rows = data.get("telemetry", [])
            for row in rows:
                try:
                    ts = datetime.fromisoformat(row['_time'].replace('Z', '+00:00')).timestamp()
                    x_coords.append(ts)
                    if metric == "Температура":
                        y_coords.append(float(row.get('temperature', 0)))
                    elif metric == "Пик-фактор":
                        y_coords.append(float(row.get('crest_factor', 0)))
                    else:
                        y_coords.append(float(row.get('vibration_rms', 0)))
                except: pass
            color = (124, 182, 255)

        if x_coords and y_coords:
            # Строим линию графика
            self.history_plot_interactive.plot(x_coords, y_coords, pen=real_pg.mkPen(color=color, width=2))
            
            # Настраиваем лимиты перемещения: пользователь сможет крутить и двигать график
            # только в пределах загруженных данных плюс небольшие отступы по бокам
            time_range = max(x_coords) - min(x_coords)
            padding = time_range * 0.05 if time_range > 0 else 60
            
            self.history_plot_interactive.getPlotItem().getViewBox().setLimits(
                xMin=min(x_coords) - padding,
                xMax=max(x_coords) + padding,
                yMin=0 # Полная блокировка ухода в минус по вертикали
            )
            
            # Возвращаем фокус на свежие данные
            self.history_plot_interactive.autoRange()
            self.history_status.setText(f"Успешно загружено {len(x_coords)} точек. Управление: Колёсико — Зум, ЛКМ — Перетаскивание, ПКМ — Сброс вида.")
        else:
            self.history_status.setText("В базе нет данных за этот временной отрезок.")
            
    def _update_analytics(self) -> None:
        state = self._selected_state(self.analytics_device_combo)
        if not state or not state.health:
            return
        health = state.health
        point = state.telemetry
        self.hi_value.setText(f"{health.hi:.2f}")
        self.zone_label.setText(f"Зона {health.risk_zone.value}: {health.risk_zone.title}")
        self.zone_label.setStyleSheet(f"color: {health.risk_zone.color}; font-weight: 700;")
        self.rul_value.setText(format_rul(health.rul_hours))
        self.confidence_label.setText(f"Достоверность {health.confidence * 100:.0f}%")
        self.diagnosis_label.setText(health.diagnosis)
        self.recommendation_label.setText(health.recommendation)

        self.forecast_plot.clear()
        hours = min(max(health.rul_hours, 2.0), 240.0)
        xs = [i * hours / 30 for i in range(31)]
        if health.rul_hours >= 9990 or health.trend_per_hour <= 0:
            ys = [health.hi for _ in xs]
        else:
            ys = [min(1.0, health.hi + health.trend_per_hour * x) for x in xs]
        self.forecast_plot.plot(xs, ys, pen=pg.mkPen("#20a08f", width=3))
        self.forecast_plot.plot(xs, [min(1.0, y + 0.07) for y in ys], pen=pg.mkPen("#ff8a3d", width=1, style=Qt.PenStyle.DotLine))
        self.forecast_plot.plot(xs, [max(0.0, y - 0.05) for y in ys], pen=pg.mkPen("#7cb6ff", width=1, style=Qt.PenStyle.DotLine))
        self.forecast_plot.addLine(y=0.85, pen=pg.mkPen("#ff4d5f", width=1, style=Qt.PenStyle.DashLine))

        self.component_plot.clear()
        if point:
            labels = ["1x", "2x", "BRG", "BB"]
            values = [point.one_x_amplitude, point.two_x_amplitude, point.bearing_band_energy, point.broadband_energy]
            bar = pg.BarGraphItem(x=list(range(len(values))), height=values, width=0.42, brush=QColor("#20a08f"), labels=labels)
            self.component_plot.addItem(bar)

    def _update_emulator_page(self) -> None:
        self._update_active_fault_table()
        self._update_emulator_table()
        self._update_emulator_charts()

    def _update_active_fault_table(self) -> None:
        state = self._selected_state(self.emulator_device_combo)
        if not state:
            self.active_fault_table.setRowCount(0)
            return
        faults = self.controller.active_faults(state.equipment.id)
        if not faults:
            self.active_fault_table.setRowCount(1)
            self._set_row(self.active_fault_table, 0, ["Нет активных дефектов", "0%", "Базовый режим: ровный тренд, низкий HI, без выраженных спектральных пиков"])
            return
        self.active_fault_table.setRowCount(len(faults))
        for row, (fault, severity) in enumerate(faults):
            self._set_row(
                self.active_fault_table,
                row,
                [fault.label, f"{severity * 100:.0f}%", self._fault_effect_text(fault)],
            )

    def _update_emulator_table(self) -> None:
        states = list(self.controller.states.values())
        self.emulator_table.setRowCount(len(states))
        for row, state in enumerate(states):
            health = state.health
            point = state.telemetry
            values = [
                state.equipment.name,
                self._active_faults_text(state.equipment.id),
                f"{point.vibration_rms:.2f}" if point else "--",
                f"{health.hi:.2f}" if health else "--",
                health.risk_zone.value if health else "--",
                f"{point.signal_quality * 100:.0f}%" if point else "--",
            ]
            self._set_row(self.emulator_table, row, values, health.risk_zone if health else None)

    def _add_emulator_chart(self, metric: str | None = None) -> None:
        if not hasattr(self, "emulator_charts_grid"):
            return
        metric = metric or "vibration"
        card = self._panel("График эмулятора")
        card.setMinimumHeight(300)
        card_layout = card.layout()
        assert isinstance(card_layout, QVBoxLayout)

        row = QHBoxLayout()
        combo = QComboBox()
        combo.setMinimumContentsLength(14)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for key, label in EMULATOR_CHART_OPTIONS:
            combo.addItem(label, key)
        selected = combo.findData(metric)
        combo.setCurrentIndex(selected if selected >= 0 else 0)
        combo.currentIndexChanged.connect(lambda _=0: self._update_emulator_charts())
        remove = QPushButton("Удалить")
        remove.clicked.connect(lambda _=False, target=card: self._remove_emulator_chart(target))
        row.addWidget(QLabel("Показатель"))
        row.addWidget(combo, 1)
        row.addWidget(remove)
        card_layout.addLayout(row)

        plot = pg.PlotWidget()
        plot.setMinimumHeight(230)
        card_layout.addWidget(plot, 1)
        self.emulator_chart_cards.append({"card": card, "combo": combo, "plot": plot})
        self._reflow_emulator_charts()
        self._update_emulator_charts()

    def _remove_emulator_chart(self, target: QFrame) -> None:
        for index, item in enumerate(list(self.emulator_chart_cards)):
            if item["card"] is target:
                self.emulator_charts_grid.removeWidget(target)
                target.setParent(None)
                target.deleteLater()
                del self.emulator_chart_cards[index]
                break
        if not self.emulator_chart_cards:
            self._add_emulator_chart("vibration")
        else:
            self._reflow_emulator_charts()
            self._update_emulator_charts()

    def _clear_emulator_charts(self) -> None:
        for item in list(self.emulator_chart_cards):
            card = item["card"]
            assert isinstance(card, QFrame)
            self.emulator_charts_grid.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self.emulator_chart_cards.clear()
        for metric in ["vibration", "hi", "spectrum", "components"]:
            self._add_emulator_chart(metric)

    def _reflow_emulator_charts(self) -> None:
        for item in self.emulator_chart_cards:
            card = item["card"]
            assert isinstance(card, QFrame)
            self.emulator_charts_grid.removeWidget(card)
        for index, item in enumerate(self.emulator_chart_cards):
            card = item["card"]
            assert isinstance(card, QFrame)
            self.emulator_charts_grid.addWidget(card, index // 2, index % 2)
        self.emulator_charts_grid.setColumnStretch(0, 1)
        self.emulator_charts_grid.setColumnStretch(1, 1)

    def _update_emulator_charts(self) -> None:
        state = self._selected_state(self.emulator_device_combo)
        for item in getattr(self, "emulator_chart_cards", []):
            combo = item["combo"]
            plot = item["plot"]
            assert isinstance(combo, QComboBox)
            assert isinstance(plot, pg.PlotWidget)
            self._draw_emulator_chart(plot, str(combo.currentData()), state)

    def _draw_emulator_chart(self, plot: pg.PlotWidget, metric: str, state: EquipmentState | None) -> None:
        title, bottom, left = self._emulator_chart_meta(metric)
        plot.clear()
        self._style_plot(plot, title, bottom, left)
        if not state:
            return
        points = state.telemetry_history[-90:]
        health_points = state.history[-90:]
        point = state.telemetry

        if metric == "vibration":
            self._plot_telemetry_series(plot, points, [p.vibration_rms for p in points], "#20a08f")
            plot.addLine(y=7.1, pen=pg.mkPen("#ff4d5f", width=1, style=Qt.PenStyle.DashLine))
        elif metric == "hi":
            self._plot_health_series(plot, health_points, [h.hi for h in health_points], "#20a08f")
            plot.addLine(y=0.85, pen=pg.mkPen("#ff4d5f", width=1, style=Qt.PenStyle.DashLine))
        elif metric == "temperature":
            self._plot_telemetry_series(plot, points, [p.temperature for p in points], "#ff8a3d")
        elif metric == "crest":
            self._plot_telemetry_series(plot, points, [p.crest_factor for p in points], "#f0c64b")
        elif metric == "quality":
            self._plot_telemetry_series(plot, points, [p.signal_quality * 100 for p in points], "#35d07f")
            self._plot_telemetry_series(plot, points, [p.packet_loss * 100 for p in points], "#ff4d5f")
        elif metric == "rpm":
            self._plot_telemetry_series(plot, points, [p.rpm for p in points], "#7cb6ff")
        elif metric == "rul":
            values = [min(420.0, h.rul_hours / 24.0) for h in health_points]
            self._plot_health_series(plot, health_points, values, "#7cb6ff")
            plot.addLine(y=30, pen=pg.mkPen("#ff8a3d", width=1, style=Qt.PenStyle.DashLine))
        elif metric == "spectrum" and point and point.spectrum:
            plot.plot(point.spectrum.frequencies, point.spectrum.amplitudes, pen=pg.mkPen("#f0c64b", width=2))
        elif metric == "components" and point:
            labels = ["1x", "2x", "BRG", "BB"]
            values = [point.one_x_amplitude, point.two_x_amplitude, point.bearing_band_energy, point.broadband_energy]
            plot.addItem(pg.BarGraphItem(x=list(range(len(values))), height=values, width=0.42, brush=QColor("#20a08f"), labels=labels))

    def _plot_telemetry_series(self, plot: pg.PlotWidget, points, values: list[float], color: str) -> None:
        if not points or not values:
            return
        x = _relative_seconds([p.timestamp for p in points])
        y = list(values)
        if len(x) == 1:
            x = [0.0, 1.0]
            y = [y[0], y[0]]
        plot.plot(x, y, pen=pg.mkPen(color, width=2))

    def _plot_health_series(self, plot: pg.PlotWidget, points, values: list[float], color: str) -> None:
        if not points or not values:
            return
        x = _relative_seconds([p.timestamp for p in points])
        y = list(values)
        if len(x) == 1:
            x = [0.0, 1.0]
            y = [y[0], y[0]]
        plot.plot(x, y, pen=pg.mkPen(color, width=2))

    def _emulator_chart_meta(self, metric: str) -> tuple[str, str, str]:
        return {
            "vibration": ("Последняя минута: V RMS", "сек", "мм/с"),
            "hi": ("Health Index после дефекта", "сек", "HI"),
            "temperature": ("Температура корпуса", "сек", "°C"),
            "crest": ("Пик-фактор", "сек", "индекс"),
            "spectrum": ("Спектр вибрации", "Гц", "ампл."),
            "components": ("Диагностические признаки узлов", "признак", "ампл."),
            "quality": ("Канал связи: качество и потери", "сек", "%"),
            "rpm": ("Обороты", "сек", "об/мин"),
            "rul": ("Остаточный ресурс", "сек", "сут"),
        }.get(metric, ("График эмулятора", "сек", "значение"))

    def _active_faults_text(self, equipment_id: str) -> str:
        faults = self.controller.active_faults(equipment_id)
        if not faults:
            return "Норма"
        return " + ".join(fault.label for fault, _ in faults)

    def _fault_effect_text(self, fault: FaultType) -> str:
        return {
            FaultType.BEARING: "BRG и пик-фактор растут, спектр получает ударную полосу подшипника",
            FaultType.IMBALANCE: "Усиливается 1x RPM, V RMS растет без сильных ударных пиков",
            FaultType.MISALIGNMENT: "Усиливается 2x RPM и нагрев, HI растет быстрее",
            FaultType.LOOSENESS: "Растет широкополосная энергия BB, график становится шумнее",
            FaultType.NOISE_LOSS: "Качество канала падает, появляются потери пакетов и широкополосный шум",
            FaultType.NONE: "Базовый режим",
        }[fault]

    def _update_equipment_tables(self) -> None:
        states = list(self.controller.states.values())
        self.equipment_table.setRowCount(len(states))
        self.sensor_table.setRowCount(len(states))
        for row, state in enumerate(states):
            e = state.equipment
            self._set_row(self.equipment_table, row, [e.id, e.name, e.kind, e.location, f"{e.rated_rpm:.0f}", f"{e.power_kw:.1f}"])
            s = state.sensor
            self._set_row(self.sensor_table, row, [s.id, e.name, s.sensor_type, s.position, s.adapter, str(s.sample_rate_hz), "включен" if s.enabled else "выключен"])

    def _update_connection_status(self) -> None:
        ok = self.controller.influx.enabled or self.controller.influx.health_check()
        if ok:
            text = f"InfluxDB: активна ({self.controller.influx.config.base_url})"
            self.connection_badge.setStyleSheet("color: #35d07f;")
        else:
            text = "InfluxDB: fallback"
            self.connection_badge.setStyleSheet("color: #f0c64b;")
        self.connection_badge.setText(text)
        suffix = " / InfluxDB запускается" if self._influx_starting else ""
        if hasattr(self, "mqtt_status"):
            if self.controller.mqtt.connected:
                self.mqtt_status.setText("MQTT Брокер: ПОДКЛЮЧЕН.")
                self.mqtt_status.setStyleSheet("color: #35d07f; font-weight: bold;")
            else:
                self.mqtt_status.setText("MQTT Брокер: ОТКЛЮЧЕН.")
                self.mqtt_status.setStyleSheet("color: #ff4d5f;")
        self.runtime_badge.setText(f"Эмулятор: {'запущен' if self.controller.running else 'пауза'}{suffix}")
        if hasattr(self, "influx_status"):
            if ok:
                self.influx_status.setText(f"InfluxDB работает: {self.controller.influx.config.base_url}, база {self.controller.influx.config.database}.")
            else:
                self.influx_status.setText(self.controller.influx.last_error or "InfluxDB не запущена. Приложение продолжает работу на локальном буфере.")
            self.influx_binary.setText(f"Ожидаемый binary: {Path(self.controller.influx.binary_path)}")

    def _set_row(self, table: QTableWidget, row: int, values: list[str], zone: RiskZone | None = None) -> None:
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            if zone:
                item.setForeground(QColor(zone.color))
            table.setItem(row, col, item)

    def _toggle_runtime(self) -> None:
        if self.controller.running:
            self.controller.stop()
            self.start_button.setText("Старт")
        else:
            self.controller.start()
            self.start_button.setText("Пауза")
        self._update_connection_status()

    def _apply_fault(self) -> None:
        equipment_id = self.emulator_device_combo.currentData()
        fault_value = self.scenario_combo.currentData()
        if equipment_id and fault_value:
            self.controller.add_fault(str(equipment_id), FaultType(str(fault_value)))
            self._refresh_after_emulator_change()

    def _remove_selected_fault(self) -> None:
        equipment_id = self.emulator_device_combo.currentData()
        if not equipment_id:
            return
        faults = self.controller.active_faults(str(equipment_id))
        if not faults:
            return
        row = self.active_fault_table.currentRow()
        if row < 0 or row >= len(faults):
            row = 0
        self.controller.remove_fault(str(equipment_id), faults[row][0])
        self._refresh_after_emulator_change()

    def _clear_fault(self) -> None:
        equipment_id = self.emulator_device_combo.currentData()
        if equipment_id:
            self.controller.clear_faults(str(equipment_id))
            self._refresh_after_emulator_change()

    def _refresh_after_emulator_change(self) -> None:
        if self.controller.running:
            for _ in range(3):
                self.controller.tick()
        self._update_emulator_page()
        self._update_analytics()
        self._update_dashboard()

    def _add_equipment(self, show_message: bool = True) -> None:
        self.controller.add_demo_equipment(
            self.new_name.text().strip(),
            self.new_location.text().strip(),
            float(self.new_rpm.value()),
            float(self.new_power.value()),
        )
        self.new_name.clear()
        self.new_location.clear()
        self._refresh_static_data()
        if show_message:
            QMessageBox.information(self, "Оборудование", "Агрегат и СВЧ-датчик добавлены в демо-контур.")

    def _start_influx_clicked(self) -> None:
        if self.controller.start_influx():
            QMessageBox.information(self, "InfluxDB", "InfluxDB запущена и отвечает на HTTP API.")
        else:
            QMessageBox.warning(self, "InfluxDB", self.controller.influx.last_error)
        self._update_connection_status()

    def _stop_influx_clicked(self) -> None:
        self.controller.stop_influx()
        self.controller.influx.enabled = False
        self._update_connection_status()

    def _start_influx_async(self) -> None:
        if self._influx_starting or self.controller.influx.enabled:
            return
        self._influx_starting = True
        self._update_connection_status()

        def runner() -> None:
            self.controller.start_influx()
            self._influx_starting = False

        threading.Thread(target=runner, name="egorus-influx-start", daemon=True).start()

    def _on_history_plot_clicked(self, event) -> None:
        """Сброс зума к исходным границам данных при клике ПКМ"""
        if event.button() == Qt.MouseButton.RightButton:
            if hasattr(self, "history_plot_interactive"):
             # Принудительно заставляем внутренний ViewBox сбросить масштаб до авто-границ
                self.history_plot_interactive.getPlotItem().getViewBox().autoRange()
                event.accept()
    
    def _toggle_mqtt_scan(self) -> None:
        if not getattr(self, "_is_scanning_mqtt", False):
            self._is_scanning_mqtt = True
            self.scan_button.setText("Остановка поиска...")
            self.scan_button.setStyleSheet("background-color: #f0c64b; color: #111820;")
            self.controller.start_mqtt_scan()
            QMessageBox.information(self, "Поиск устройств", "Сетевой порт открыт.\nВключите ваш физический датчик, чтобы он начал отправлять данные. Программа автоматически перехватит сигнал.")
        else:
            self._stop_mqtt_scan_ui()

    def _stop_mqtt_scan_ui(self) -> None:
        self._is_scanning_mqtt = False
        self.scan_button.setText("Сканировать сеть (Поиск новых датчиков)")
        self.scan_button.setStyleSheet("")
        self.controller.stop_mqtt_scan()

def _relative_seconds(timestamps) -> list[float]:
    if not timestamps:
        return []
    # Переводим всё в UTC формат
    ts_utc = [t.astimezone(timezone.utc) for t in timestamps]
    # Находим САМУЮ РАННЮЮ точку в этом наборе, чтобы график железно начинался от 0
    first = min(ts_utc)
    return [(t - first).total_seconds() for t in ts_utc]
