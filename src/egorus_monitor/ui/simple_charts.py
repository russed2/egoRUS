from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QSizePolicy


@dataclass(slots=True)
class SimplePen:
    color: QColor
    width: int = 2
    style: Qt.PenStyle = Qt.PenStyle.SolidLine


@dataclass(slots=True)
class BarGraphItem:
    x: list[float]
    height: list[float]
    width: float = 0.58
    brush: QColor | str = "#20a08f"
    labels: list[str] | None = None


def setConfigOptions(**_: Any) -> None:
    return None


def mkPen(color: str | QColor, width: int = 2, style: Qt.PenStyle = Qt.PenStyle.SolidLine) -> SimplePen:
    return SimplePen(QColor(color), width, style)


class _Axis:
    def __init__(self, owner: "PlotWidget", name: str) -> None:
        self.owner = owner
        self.name = name

    def setPen(self, color: str | QColor) -> None:
        self.owner.axis_color = QColor(color)

    def setTicks(self, ticks: list[list[tuple[int, str]]]) -> None:
        if ticks:
            self.owner.tick_labels = {float(x): label for x, label in ticks[0]}
            self.owner.update()


class _PlotItem:
    def __init__(self, owner: "PlotWidget") -> None:
        self.owner = owner

    def getAxis(self, name: str) -> _Axis:
        return _Axis(self.owner, name)


class PlotWidget(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(210)
        self.title = ""
        self.labels = {"bottom": "", "left": ""}
        self.axis_color = QColor("#516977")
        self.grid_color = QColor(60, 84, 96, 70)
        self.text_color = QColor("#d8e2e7")
        self.series: list[tuple[list[float], list[float], SimplePen]] = []
        self.hlines: list[tuple[float, SimplePen]] = []
        self.bars: list[BarGraphItem] = []
        self.tick_labels: dict[float, str] = {}
        self.setStyleSheet("background: #121b23; border: 1px solid #263944; border-radius: 6px;")

    def setTitle(self, title: str, color: str = "#f0f6f6", size: str = "12pt") -> None:
        self.title = title
        self.text_color = QColor(color)
        self.update()

    def showGrid(self, x: bool = True, y: bool = True, alpha: float = 0.22) -> None:
        self.grid_color = QColor(60, 84, 96, int(255 * alpha))
        self.update()

    def setLabel(self, side: str, text: str) -> None:
        self.labels[side] = text
        self.update()

    def getPlotItem(self) -> _PlotItem:
        return _PlotItem(self)

    def getAxis(self, name: str) -> _Axis:
        return _Axis(self, name)

    def clear(self) -> None:
        self.series.clear()
        self.hlines.clear()
        self.bars.clear()
        self.tick_labels.clear()
        self.update()

    def plot(self, x: list[float], y: list[float], pen: SimplePen | None = None, name: str | None = None) -> None:
        if not x or not y:
            return
        self.series.append((list(x), list(y), pen or mkPen("#20a08f", width=2)))
        self.update()

    def addLine(self, y: float, pen: SimplePen | None = None) -> None:
        self.hlines.append((float(y), pen or mkPen("#ff4d5f", width=1, style=Qt.PenStyle.DashLine)))
        self.update()

    def addItem(self, item: BarGraphItem) -> None:
        self.bars.append(item)
        if item.labels:
            self.tick_labels = {float(x): label for x, label in zip(item.x, item.labels, strict=False)}
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(12, 10, -12, -12)
        title_h = 28
        plot = QRectF(rect.left() + 42, rect.top() + title_h, rect.width() - 52, rect.height() - title_h - 32)
        if plot.width() < 20 or plot.height() < 20:
            return

        painter.setPen(self.text_color)
        title_font = QFont()
        title_font.setPointSize(10)
        title_font.setBold(False)
        painter.setFont(title_font)
        painter.drawText(QRectF(rect.left(), rect.top(), rect.width(), 22), Qt.AlignmentFlag.AlignCenter, self.title)

        x_min, x_max, y_min, y_max = self._bounds()
        if x_max <= x_min:
            x_max = x_min + 1.0
        if y_max <= y_min:
            y_max = y_min + 1.0
        padding = (y_max - y_min) * 0.08
        y_min = max(0.0, y_min - padding)
        y_max += padding

        self._draw_grid(painter, plot, x_min, x_max, y_min, y_max)
        self._draw_bars(painter, plot, x_min, x_max, y_min, y_max)
        self._draw_lines(painter, plot, x_min, x_max, y_min, y_max)
        self._draw_axes(painter, plot, x_min, x_max, y_min, y_max)

    def _bounds(self) -> tuple[float, float, float, float]:
        xs: list[float] = []
        ys: list[float] = []
        for x, y, _ in self.series:
            xs.extend(x)
            ys.extend(y)
        for bar in self.bars:
            pad = max(0.5, bar.width)
            xs.extend(float(v) - pad for v in bar.x)
            xs.extend(float(v) + pad for v in bar.x)
            ys.extend(float(v) for v in bar.height)
            ys.append(0.0)
        ys.extend(y for y, _ in self.hlines)
        if not xs:
            xs = [0.0, 1.0]
        if not ys:
            ys = [0.0, 1.0]
        return min(xs), max(xs), min(ys), max(ys)

    def _draw_grid(self, painter: QPainter, plot: QRectF, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
        painter.setPen(QPen(self.grid_color, 1))
        for i in range(6):
            x = plot.left() + plot.width() * i / 5
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            y = plot.top() + plot.height() * i / 5
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

    def _draw_axes(self, painter: QPainter, plot: QRectF, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
        painter.setPen(QPen(self.axis_color, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(plot)
        painter.setPen(QColor("#cfe2e7"))
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        if self.tick_labels:
            for x_value, label in sorted(self.tick_labels.items()):
                x = self._map_x(x_value, plot, x_min, x_max)
                painter.drawText(QRectF(x - 34, plot.bottom() + 5, 68, 18), Qt.AlignmentFlag.AlignCenter, label)
        else:
            for i in range(1, 6):
                x_value = x_min + (x_max - x_min) * i / 5
                x = plot.left() + plot.width() * i / 5
                painter.drawText(QRectF(x - 30, plot.bottom() + 5, 60, 18), Qt.AlignmentFlag.AlignCenter, f"{x_value:.1f}")
        for i in range(1, 6):
            y_value = y_max - (y_max - y_min) * i / 5
            y = plot.top() + plot.height() * i / 5
            painter.drawText(QRectF(plot.left() - 42, y - 9, 36, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{y_value:.1f}")
        painter.setPen(QColor("#8ea0aa"))
        painter.drawText(QRectF(plot.left(), plot.bottom() + 20, plot.width(), 18), Qt.AlignmentFlag.AlignCenter, self.labels.get("bottom", ""))
        painter.save()
        painter.translate(plot.left() - 36, plot.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-plot.height() / 2, -10, plot.height(), 18), Qt.AlignmentFlag.AlignCenter, self.labels.get("left", ""))
        painter.restore()

    def _draw_lines(self, painter: QPainter, plot: QRectF, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
        for y, pen in self.hlines:
            painter.setPen(QPen(pen.color, pen.width, pen.style))
            py = self._map_y(y, plot, y_min, y_max)
            painter.drawLine(QPointF(plot.left(), py), QPointF(plot.right(), py))
        for xs, ys, pen in self.series:
            painter.setPen(QPen(pen.color, pen.width, pen.style))
            points = [QPointF(self._map_x(x, plot, x_min, x_max), self._map_y(y, plot, y_min, y_max)) for x, y in zip(xs, ys, strict=False)]
            for a, b in zip(points, points[1:], strict=False):
                painter.drawLine(a, b)

    def _draw_bars(self, painter: QPainter, plot: QRectF, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
        for bar in self.bars:
            color = QColor(bar.brush) if not isinstance(bar.brush, QColor) else bar.brush
            painter.setPen(QPen(QColor("#0f171f"), 1))
            painter.setBrush(color)
            if len(bar.x) > 1:
                sorted_x = sorted(float(x) for x in bar.x)
                gaps = [b - a for a, b in zip(sorted_x, sorted_x[1:], strict=False) if b > a]
                step = min(gaps) if gaps else 1.0
            else:
                step = 1.0
            step_pixels = plot.width() * step / max(x_max - x_min, 1e-9)
            width = min(step_pixels * max(0.2, bar.width), step_pixels * 0.62, plot.width() * 0.10)
            width = max(8.0, width)
            baseline = self._map_y(0.0, plot, y_min, y_max)
            for x, height in zip(bar.x, bar.height, strict=False):
                cx = self._map_x(float(x), plot, x_min, x_max)
                y = self._map_y(float(height), plot, y_min, y_max)
                top = min(y, baseline)
                bar_height = max(1.0, abs(baseline - y))
                painter.drawRoundedRect(QRectF(cx - width / 2, top, width, bar_height), 3, 3)

    def _map_x(self, value: float, plot: QRectF, low: float, high: float) -> float:
        return plot.left() + (value - low) / max(high - low, 1e-9) * plot.width()

    def _map_y(self, value: float, plot: QRectF, low: float, high: float) -> float:
        return plot.bottom() - (value - low) / max(high - low, 1e-9) * plot.height()
