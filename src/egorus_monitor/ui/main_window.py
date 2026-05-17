from __future__ import annotations

import csv
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
    QFileDialog,
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

    def closeEvent(self, event) -> None:  
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
            ("Обзор оборудования", "Состояние всех агрегатов"),
            ("Реальное время", "Текущие значения и графики"),
            ("История", "Архив телеметрии"),
            ("Предиктивная аналитика", "HI, RUL и дефекты"),
            ("Эмулятор", "Сценарии и поломки"),
            ("Оборудование", "Устройства и датчики"),
            ("Подключения", "Управление сетью и БД"),
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
        page = self._page("Обзор оборудования")
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
        fleet_panel = self._panel("Список агрегатов")
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

        self.overview_plot = real_pg.PlotWidget()
        self.overview_plot.setBackground("#121b23")
        self._style_plot(self.overview_plot, "Тренд индекса состояния HI", "сек", "HI")
        
        self.overview_plot.getPlotItem().getAxis("left").enableAutoSIPrefix(False)
        self.overview_plot.getPlotItem().setMenuEnabled(False)
        self.overview_plot.getPlotItem().getViewBox().setMenuEnabled(False)
        self.overview_plot.getPlotItem().getViewBox().setLimits(xMin=0, yMin=0, yMax=1.05)

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
        self.real_plot = real_pg.PlotWidget()
        self.real_plot.setBackground("#121b23")
        plots.addWidget(self.real_plot, 3)
        
        self.spectrum_plot = real_pg.PlotWidget()
        self.spectrum_plot.setBackground("#121b23")
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

        self.export_button = QPushButton("Экспорт в CSV")
        self.export_button.clicked.connect(self._export_history_csv)
        self.export_button.setEnabled(False)
        
        controls.addWidget(QLabel("Агрегат:"))
        controls.addWidget(self.history_device_combo, 2)
        controls.addWidget(QLabel("Показатель:"))
        controls.addWidget(self.history_metric_combo, 1)
        controls.addWidget(QLabel("Период:"))
        controls.addWidget(self.history_period_combo, 1)
        controls.addWidget(refresh)
        controls.addWidget(self.export_button)
        layout.addLayout(controls)

        axis = real_pg.DateAxisItem(orientation='bottom')
        self.history_plot_interactive = real_pg.PlotWidget(axisItems={'bottom': axis})
        self.history_plot_interactive.setBackground("#121b23")
        self.history_plot_interactive.showGrid(x=True, y=True, alpha=0.3)

        self.history_plot_interactive.getPlotItem().setMenuEnabled(False)
        self.history_plot_interactive.getPlotItem().getViewBox().setMenuEnabled(False)
        self.history_plot_interactive.getPlotItem().getViewBox().setLimits(yMin=0)
        self.history_plot_interactive.scene().sigMouseClicked.connect(self._on_history_plot_clicked)
        
        layout.addWidget(self.history_plot_interactive, 1)

        self.history_status = QLabel("Выберите параметры и нажмите 'Загрузить из БД'.")
        self.history_status.setObjectName("Muted")
        layout.addWidget(self.history_status)
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
        self.forecast_plot = real_pg.PlotWidget()
        self.forecast_plot.setBackground("#121b23")
        self._style_plot(self.forecast_plot, "Прогноз HI до критического порога", "часы", "HI")
        self.forecast_plot.getPlotItem().setMenuEnabled(False)
        self.forecast_plot.getPlotItem().getViewBox().setMenuEnabled(False)
        self.forecast_plot.getPlotItem().getViewBox().setLimits(xMin=0, yMin=0, yMax=1.05)
        self.forecast_plot.scene().sigMouseClicked.connect(self._on_forecast_plot_clicked)
        plots.addWidget(self.forecast_plot, 3)
        
        self.component_plot = real_pg.PlotWidget()
        self.component_plot.setBackground("#121b23")
        self._style_plot(self.component_plot, "Диагностические признаки узлов", "узел", "ампл.")
        plots.addWidget(self.component_plot, 2)
        layout.addLayout(plots, 1)
        return page

    def _emulator_page(self) -> QWidget:
        page = self._page("Эмулятор дефектов")
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

        add_panel = self._panel("Добавить агрегат в контур")
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

    def _style_plot(self, plot: real_pg.PlotWidget, title: str, bottom: str, left: str) -> None:
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
            
        if getattr(self, "_is_scanning_mqtt", False):
            if self.controller.discovered_devices:
                found_id = list(self.controller.discovered_devices.keys())[0]
                self.new_name.setText(found_id) 
                self._stop_mqtt_scan_ui()       
                self.controller.discovered_devices.clear()
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

        if not hasattr(self, '_overview_lines'):
            self.overview_plot.clear()
            self.overview_plot.setYRange(0.0, 1.05, padding=0)
            self.overview_plot.addLine(y=0.85, pen=real_pg.mkPen("#ff4d5f", width=1, style=Qt.PenStyle.DashLine))
            colors = ["#20a08f", "#f0c64b", "#ff8a3d", "#7cb6ff", "#d879ff"]
            self._overview_lines = [self.overview_plot.plot([], [], pen=real_pg.mkPen(c, width=2)) for c in colors]

        for index, state in enumerate(states[:5]):
            if not state.history:
                continue
                
            x = _relative_seconds([h.timestamp for h in state.history])
            y = [h.hi for h in state.history]
            
            if len(x) == 1:
                x, y = [0.0, 1.0], [y[0], y[0]]
            self._overview_lines[index].setData(x, y)

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

        if not hasattr(self, '_rt_line'):
            self.real_plot.clear()
            self._style_plot(self.real_plot, "Последняя минута: виброскорость", "сек", "мм/с")
            self._rt_line = self.real_plot.plot([], [], pen=real_pg.mkPen("#20a08f", width=3))
            self.real_plot.addLine(y=7.1, pen=real_pg.mkPen("#ff4d5f", width=1, style=Qt.PenStyle.DashLine))
            
            self.spectrum_plot.clear()
            self._style_plot(self.spectrum_plot, "Спектр вибрации", "Гц", "ампл.")
            self._rt_spec_line = self.spectrum_plot.plot([], [], pen=real_pg.mkPen("#f0c64b", width=2))

        try:
            if points:
                x = _relative_seconds([p.timestamp for p in points])
                y = [p.vibration_rms for p in points]
                if len(x) == 1: 
                    x, y = [0.0, 1.0], [y[0], y[0]]
                self._rt_line.setData(x, y)
                
            if point.spectrum:
                self._rt_spec_line.setData(point.spectrum.frequencies, point.spectrum.amplitudes)
        except:
            pass

    def _update_history(self) -> None:
        state = self._selected_state(self.history_device_combo)
        if not state or not hasattr(self, "history_plot_interactive"):
            return
            
        metric = self.history_metric_combo.currentText()
        minutes_back = self.history_period_combo.currentData()
        
        self.history_status.setText(f"Запрос к InfluxDB за последние {minutes_back} мин...")
        self.history_plot_interactive.clear()
        
        self.history_plot_interactive.getPlotItem().getViewBox().setLimits(xMin=None, xMax=None, yMin=0)
        
        data = self.controller.influx.read_history(state.equipment.id, minutes_back=int(minutes_back))

        self._last_history_data = data
        self._last_history_metric = metric
        self._last_history_eq_name = state.equipment.name
        
        if not isinstance(data, dict) or (not data.get("telemetry") and not data.get("health")):
            self.history_status.setText("Данные за выбранный период в базе не найдены.")
            self.export_button.setEnabled(False)
            return
        
        self.export_button.setEnabled(True)

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
            self.history_plot_interactive.plot(x_coords, y_coords, pen=real_pg.mkPen(color=color, width=2))
            
            time_range = max(x_coords) - min(x_coords)
            padding = time_range * 0.05 if time_range > 0 else 60
            
            self.history_plot_interactive.getPlotItem().getViewBox().setLimits(
                xMin=min(x_coords) - padding,
                xMax=max(x_coords) + padding,
                yMin=0 
            )
            
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

        current_eq_id = state.equipment.id
        is_new_eq = getattr(self, '_last_analytics_eq', None) != current_eq_id

        # === 1. ЛЕВЫЙ ГРАФИК (Прогноз HI) ===
        try:
            # 1. Горизонт планирования: если всё хорошо, смотрим на 30 дней (720 часов) вперед.
            # Если всё плохо - ось X сжимается до точного времени смерти (RUL).
            max_plot_hours = min(health.rul_hours, 720.0) 
            if max_plot_hours < 2.0:
                max_plot_hours = 2.0
                
            self.forecast_plot.setXRange(0, max_plot_hours, padding=0.05)
            
            xs = [i * max_plot_hours / 30 for i in range(31)]
            ys, ys_upper, ys_lower = [], [], []
            
            uncertainty_factor = 1.0 - health.confidence 

            # 2. КИНЕМАТИЧЕСКАЯ ФИЗИЧЕСКАЯ МОДЕЛЬ
            # v - текущая реальная скорость износа (тренд)
            v = max(0.0, health.trend_per_hour)
            rul = health.rul_hours

            # c - ускорение деградации. 
            # Вычисляется ТОЛЬКО если линейного тренда не хватает, чтобы добить станок к сроку RUL.
            if rul < 9990 and health.hi < 1.0:
                expected_linear_end = health.hi + v * rul
                if expected_linear_end < 1.0:
                    c = (1.0 - expected_linear_end) / (rul ** 2)
                else:
                    c = 0.0 # Тренд уже достаточно резкий
            else:
                c = 0.0

            for x in xs:
                if rul >= 9990 or health.hi >= 1.0:
                    y = health.hi
                else:
                    # y = начальная точка + (скорость * время) + (ускорение * время в квадрате)
                    y = health.hi + v * x + c * (x ** 2)

                # Погрешность (конус) растет линейно от 0 до макс. значения к концу графика
                current_cone = (x / max_plot_hours) * uncertainty_factor * 0.15

                ys.append(min(1.0, max(0.0, y)))
                ys_upper.append(min(1.0, y + current_cone))
                ys_lower.append(max(0.0, y - current_cone))

            if is_new_eq or not hasattr(self, '_forecast_lines'):
                self.forecast_plot.clear()
                self.forecast_plot.getPlotItem().getViewBox().setYRange(0.0, 1.05, padding=0)
                
                y_axis = self.forecast_plot.getPlotItem().getAxis("left")
                ticks_list = [(i / 10.0, f"{i / 10.0:.1f}") for i in range(11)]
                y_axis.setTicks([ticks_list])

                l1 = self.forecast_plot.plot(xs, ys, pen=real_pg.mkPen("#20a08f", width=3))
                l2 = self.forecast_plot.plot(xs, ys_upper, pen=real_pg.mkPen("#ff8a3d", width=1, style=Qt.PenStyle.DotLine))
                l3 = self.forecast_plot.plot(xs, ys_lower, pen=real_pg.mkPen("#7cb6ff", width=1, style=Qt.PenStyle.DotLine))
                self.forecast_plot.addLine(y=0.85, pen=real_pg.mkPen("#ff4d5f", width=1, style=Qt.PenStyle.DashLine))
                
                self._forecast_lines = (l1, l2, l3)
            else:
                l1, l2, l3 = self._forecast_lines
                l1.setData(xs, ys)
                l2.setData(xs, ys_upper)
                l3.setData(xs, ys_lower)
                
        except Exception as e:
            print(f"Ошибка отрисовки прогноза: {e}")

        # === 2. ПРАВЫЙ ГРАФИК (Признаки узлов) ===
        try:
            if point:
                labels = ["1x", "2x", "BRG", "BB"]
                values = [point.one_x_amplitude, point.two_x_amplitude, point.bearing_band_energy, point.broadband_energy]
                
                if is_new_eq or not hasattr(self, '_component_bar'):
                    self.component_plot.clear()
                    
                    self._component_bar = real_pg.BarGraphItem(
                        x=list(range(len(values))), 
                        height=values, 
                        width=0.42, 
                        brush=real_pg.mkBrush("#20a08f")
                    )
                    self.component_plot.addItem(self._component_bar)
                    
                    x_axis = self.component_plot.getPlotItem().getAxis("bottom")
                    x_ticks = [(i, labels[i]) for i in range(len(labels))]
                    x_axis.setTicks([x_ticks])
                else:
                    self._component_bar.setOpts(height=values)
                    
        except Exception:
            pass

        self._last_analytics_eq = current_eq_id

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

        plot = real_pg.PlotWidget()
        plot.setBackground("#121b23")
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
            assert isinstance(plot, real_pg.PlotWidget)
            self._draw_emulator_chart(plot, str(combo.currentData()), state)

    def _draw_emulator_chart(self, plot: real_pg.PlotWidget, metric: str, state: EquipmentState | None) -> None:
        title, bottom, left = self._emulator_chart_meta(metric)
        
        current_metric = getattr(plot, '_current_metric', None)
        
        if current_metric != metric:
            plot.clear()
            self._style_plot(plot, title, bottom, left)
            plot._current_metric = metric
            
            if metric == "vibration":
                plot._l1 = plot.plot([], [], pen=real_pg.mkPen("#20a08f", width=2))
                plot.addLine(y=7.1, pen=real_pg.mkPen("#ff4d5f", width=1, style=Qt.PenStyle.DashLine))
            elif metric == "hi":
                plot._l1 = plot.plot([], [], pen=real_pg.mkPen("#20a08f", width=2))
                plot.addLine(y=0.85, pen=real_pg.mkPen("#ff4d5f", width=1, style=Qt.PenStyle.DashLine))
            elif metric == "temperature":
                plot._l1 = plot.plot([], [], pen=real_pg.mkPen("#ff8a3d", width=2))
            elif metric == "crest":
                plot._l1 = plot.plot([], [], pen=real_pg.mkPen("#f0c64b", width=2))
            elif metric == "quality":
                plot._l1 = plot.plot([], [], pen=real_pg.mkPen("#35d07f", width=2))
                plot._l2 = plot.plot([], [], pen=real_pg.mkPen("#ff4d5f", width=2))
            elif metric == "rpm":
                plot._l1 = plot.plot([], [], pen=real_pg.mkPen("#7cb6ff", width=2))
            elif metric == "rul":
                plot._l1 = plot.plot([], [], pen=real_pg.mkPen("#7cb6ff", width=2))
                plot.addLine(y=30, pen=real_pg.mkPen("#ff8a3d", width=1, style=Qt.PenStyle.DashLine))
            elif metric == "spectrum":
                plot._l1 = plot.plot([], [], pen=real_pg.mkPen("#f0c64b", width=2))
            elif metric == "components":
                labels = ["1x", "2x", "BRG", "BB"]
                plot._bar = real_pg.BarGraphItem(x=list(range(4)), height=[0,0,0,0], width=0.42, brush=real_pg.mkBrush("#20a08f"))
                plot.addItem(plot._bar)
                x_axis = plot.getPlotItem().getAxis("bottom")
                x_axis.setTicks([[(i, labels[i]) for i in range(4)]])

        if not state:
            return
            
        points = state.telemetry_history[-90:]
        health_points = state.history[-90:]
        point = state.telemetry

        try:
            if metric == "vibration" and points:
                x = _relative_seconds([p.timestamp for p in points])
                y = [p.vibration_rms for p in points]
                if len(x) == 1: x, y = [0.0, 1.0], [y[0], y[0]]
                plot._l1.setData(x, y)
            elif metric == "hi" and health_points:
                x = _relative_seconds([p.timestamp for p in health_points])
                y = [h.hi for h in health_points]
                if len(x) == 1: x, y = [0.0, 1.0], [y[0], y[0]]
                plot._l1.setData(x, y)
            elif metric == "temperature" and points:
                x = _relative_seconds([p.timestamp for p in points])
                y = [p.temperature for p in points]
                if len(x) == 1: x, y = [0.0, 1.0], [y[0], y[0]]
                plot._l1.setData(x, y)
            elif metric == "crest" and points:
                x = _relative_seconds([p.timestamp for p in points])
                y = [p.crest_factor for p in points]
                if len(x) == 1: x, y = [0.0, 1.0], [y[0], y[0]]
                plot._l1.setData(x, y)
            elif metric == "quality" and points:
                x = _relative_seconds([p.timestamp for p in points])
                y1 = [p.signal_quality * 100 for p in points]
                y2 = [p.packet_loss * 100 for p in points]
                if len(x) == 1: 
                    x = [0.0, 1.0]
                    y1, y2 = [y1[0], y1[0]], [y2[0], y2[0]]
                plot._l1.setData(x, y1)
                plot._l2.setData(x, y2)
            elif metric == "rpm" and points:
                x = _relative_seconds([p.timestamp for p in points])
                y = [p.rpm for p in points]
                if len(x) == 1: x, y = [0.0, 1.0], [y[0], y[0]]
                plot._l1.setData(x, y)
            elif metric == "rul" and health_points:
                x = _relative_seconds([p.timestamp for p in health_points])
                y = [min(420.0, h.rul_hours / 24.0) for h in health_points]
                if len(x) == 1: x, y = [0.0, 1.0], [y[0], y[0]]
                plot._l1.setData(x, y)
            elif metric == "spectrum" and point and point.spectrum:
                plot._l1.setData(point.spectrum.frequencies, point.spectrum.amplitudes)
            elif metric == "components" and point:
                values = [point.one_x_amplitude, point.two_x_amplitude, point.bearing_band_energy, point.broadband_energy]
                plot._bar.setOpts(height=values)
        except:
            pass

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
        if event.button() == Qt.MouseButton.RightButton:
            if hasattr(self, "history_plot_interactive"):
                self.history_plot_interactive.getPlotItem().getViewBox().autoRange()
                event.accept()

    def _on_forecast_plot_clicked(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            if hasattr(self, "forecast_plot"):
                self.forecast_plot.getPlotItem().getViewBox().enableAutoRange(axis='x', enable=True)
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

    def _export_history_csv(self) -> None:
        if not getattr(self, "_last_history_data", None):
            return

        data = self._last_history_data
        metric = self._last_history_metric
        eq_name = self._last_history_eq_name

        rows = []
        if metric == "Health Index":
            for row in data.get("health", []):
                rows.append([row.get('_time', ''), row.get('hi', 0)])
        else:
            for row in data.get("telemetry", []):
                val = 0
                if metric == "Температура": val = row.get('temperature', 0)
                elif metric == "Пик-фактор": val = row.get('crest_factor', 0)
                else: val = row.get('vibration_rms', 0)
                rows.append([row.get('_time', ''), val])

        if not rows:
            QMessageBox.warning(self, "Экспорт", "Нет данных для экспорта.")
            return

        clean_name = eq_name.replace(" ", "_").replace("/", "-")
        default_filename = f"Export_{clean_name}_{metric}.csv"
        
        filepath, _ = QFileDialog.getSaveFileName(
            self, 
            "Сохранить данные в CSV", 
            default_filename, 
            "CSV-таблицы (*.csv);;Все файлы (*)"
        )

        if not filepath:
            return 

        try:
            with open(filepath, mode='w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f, delimiter=';')
                writer.writerow(["Время InfluxDB (UTC)", metric]) 
                for r in rows:
                    formatted_val = str(r[1]).replace('.', ',')
                    writer.writerow([r[0], formatted_val])
                    
            QMessageBox.information(self, "Успех", f"Данные успешно экспортированы!\nФайл сохранен: {filepath}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл:\n{e}")

def _relative_seconds(timestamps) -> list[float]:
    if not timestamps:
        return []
    ts_utc = [t.astimezone(timezone.utc) for t in timestamps]
    first = min(ts_utc)
    return [(t - first).total_seconds() for t in ts_utc]