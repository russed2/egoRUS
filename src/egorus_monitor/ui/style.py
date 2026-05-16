APP_STYLESHEET = """
* {
    font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
    color: #d8e2e7;
    font-size: 13px;
}
QMainWindow, QWidget {
    background: #111820;
}
QFrame#Sidebar {
    background: #0a1016;
    border-right: 1px solid #263541;
}
QLabel#AppTitle {
    color: #f2f7f7;
    font-size: 19px;
    font-weight: 700;
    letter-spacing: 0px;
}
QLabel#AppSubtitle {
    color: #8ea0aa;
    font-size: 11px;
}
QPushButton#NavButton {
    background: transparent;
    color: #aebdc5;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 10px 12px;
    text-align: left;
}
QPushButton#NavButton:hover {
    background: #14212b;
    border-color: #263945;
}
QPushButton#NavButton:checked {
    background: #1d313b;
    color: #ffffff;
    border-color: #3f5f6b;
}
QPushButton {
    background: #263943;
    color: #eef6f6;
    border: 1px solid #3d5965;
    border-radius: 6px;
    padding: 8px 12px;
}
QPushButton:hover {
    background: #304855;
    border-color: #5b7d8d;
}
QPushButton:pressed {
    background: #1d2d36;
}
QPushButton#DangerButton {
    background: #4b2028;
    border-color: #7c3340;
}
QPushButton#AccentButton {
    background: #0f6b62;
    border-color: #20a08f;
}
QFrame#Panel, QFrame#Card {
    background: #17222b;
    border: 1px solid #2b3c46;
    border-radius: 8px;
}
QFrame#KpiOk {
    background: #13251f;
    border: 1px solid #295c46;
    border-radius: 8px;
}
QFrame#KpiWarn {
    background: #2a2414;
    border: 1px solid #6f5a22;
    border-radius: 8px;
}
QFrame#KpiCrit {
    background: #2c171c;
    border: 1px solid #76313e;
    border-radius: 8px;
}
QLabel#PageTitle {
    font-size: 24px;
    font-weight: 700;
    color: #f7fbfb;
}
QLabel#SectionTitle {
    font-size: 15px;
    font-weight: 700;
    color: #f2f8f8;
}
QLabel#MetricValue {
    font-size: 30px;
    font-weight: 800;
    color: #ffffff;
}
QLabel#MetricLabel {
    color: #8fa3ad;
    font-size: 11px;
}
QLabel#Muted {
    color: #8fa3ad;
}
QTableWidget, QTreeWidget {
    background: #121b23;
    alternate-background-color: #16242d;
    border: 1px solid #2a3b45;
    border-radius: 6px;
    gridline-color: #283842;
    selection-background-color: #254653;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: #20303a;
    color: #cbd8de;
    border: none;
    border-right: 1px solid #334852;
    padding: 7px;
    font-weight: 700;
}
QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox {
    background: #0f171f;
    color: #e7eeee;
    border: 1px solid #334852;
    border-radius: 6px;
    padding: 7px;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QProgressBar {
    background: #0e161d;
    border: 1px solid #2d404c;
    border-radius: 5px;
    text-align: center;
    height: 18px;
}
QProgressBar::chunk {
    background: #20a08f;
    border-radius: 4px;
}
QTabWidget::pane {
    border: 1px solid #2c3e49;
    border-radius: 8px;
}
QTabBar::tab {
    background: #14202a;
    color: #aebbc3;
    padding: 9px 14px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:selected {
    background: #203541;
    color: #ffffff;
}
"""
