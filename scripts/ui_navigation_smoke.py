from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from PIL import ImageGrab


def run_exe_smoke(exe: Path, screenshots: Path) -> int:
    import win32api
    import win32con
    import win32gui
    import win32process

    screenshots.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    hwnd = _wait_for_window(win32gui, win32process, process.pid)
    if not hwnd:
        process.terminate()
        raise RuntimeError("Main window was not created")

    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(1.5)
    _grab_window(win32gui, hwnd, screenshots / "exe_00_start.png")

    labels = [
        "overview",
        "realtime",
        "history",
        "analytics",
        "emulator",
        "equipment",
        "connections",
    ]
    for index, label in enumerate(labels):
        if process.poll() is not None:
            raise RuntimeError(f"Process exited before nav click {label}: code={process.returncode}")
        rect = win32gui.GetWindowRect(hwnd)
        x = rect[0] + 120
        y = rect[1] + 146 + index * 49
        _click(win32api, win32con, x, y)
        time.sleep(1.2)
        if process.poll() is not None:
            raise RuntimeError(f"Process exited after nav click {label}: code={process.returncode}")
        _grab_window(win32gui, hwnd, screenshots / f"exe_{index + 1:02d}_{label}.png")

    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    return 0


def _wait_for_window(win32gui, win32process, pid: int, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = []

        def callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            title = win32gui.GetWindowText(hwnd)
            if window_pid == pid and title:
                result.append(hwnd)
                return False
            return True

        win32gui.EnumWindows(callback, None)
        if result:
            return result[0]
        if subprocess_is_done(pid):
            return None
        time.sleep(0.2)
    return None


def subprocess_is_done(pid: int) -> bool:
    try:
        import psutil

        return not psutil.pid_exists(pid)
    except Exception:
        return False


def _click(win32api, win32con, x: int, y: int) -> None:
    win32api.SetCursorPos((x, y))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)


def _grab_window(win32gui, hwnd, path: Path) -> None:
    rect = win32gui.GetWindowRect(hwnd)
    image = ImageGrab.grab(bbox=rect)
    image.save(path)


def run_source_smoke(screenshots: Path) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    project_src = Path(__file__).resolve().parents[1] / "src"
    if str(project_src) not in sys.path:
        sys.path.insert(0, str(project_src))
    from PySide6.QtWidgets import QApplication

    from egorus_monitor.ui.main_window import MainWindow

    screenshots.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(start_influx=False)
    window.resize(1440, 900)
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        window.show()
    for _ in range(6):
        window._on_tick()
        app.processEvents()
    labels = [
        "overview",
        "realtime",
        "history",
        "analytics",
        "emulator",
        "equipment",
        "connections",
    ]
    for index, label in enumerate(labels):
        window._select_page(index)
        app.processEvents()
        for _ in range(3):
            window._on_tick()
            app.processEvents()
        window.grab().save(str(screenshots / f"source_{index + 1:02d}_{label}.png"))
        print(f"source page {index} OK", flush=True)

    window.emulator_device_combo.setCurrentIndex(0)
    for idx in range(window.scenario_combo.count()):
        window.scenario_combo.setCurrentIndex(idx)
        window._apply_fault()
        for _ in range(2):
            window._on_tick()
            app.processEvents()
    window._select_page(4)
    app.processEvents()
    window.grab().save(str(screenshots / "source_emulator_faults.png"))
    window._clear_fault()
    window.new_name.setText("Тестовый двигатель")
    window.new_location.setText("Стенд проверки")
    window._add_equipment(show_message=False)
    window._on_tick()
    app.processEvents()
    print("source equipment add OK", flush=True)
    window.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["source", "exe"], required=True)
    parser.add_argument("--exe", type=Path, default=Path("dist/EgoRUSMonitor/EgoRUSMonitor.exe"))
    parser.add_argument("--screenshots", type=Path, default=Path("artifacts/screenshots"))
    args = parser.parse_args()
    if args.mode == "source":
        return run_source_smoke(args.screenshots)
    return run_exe_smoke(args.exe.resolve(), args.screenshots)


if __name__ == "__main__":
    raise SystemExit(main())
