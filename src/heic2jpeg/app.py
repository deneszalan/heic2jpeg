"""Application entry point."""

from __future__ import annotations

import os
import sys
import time
import traceback

from . import APP_ID, APP_NAME, __version__

STARTED = time.perf_counter()


def _prepare_windows() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # Makes Windows show our icon (not Python's) in the taskbar.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ID}.Converter.{__version__}")
    except Exception:  # noqa: BLE001
        pass


def _ensure_std_streams() -> None:
    # A windowed .exe has no console; give libraries somewhere harmless to write.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def _install_error_dialog() -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    def handle(exc_type, exc, tb):
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        if sys.__stderr__ is not None:
            sys.__stderr__.write(details)
        if QApplication.instance() is None:
            return
        box = QMessageBox(QMessageBox.Icon.Critical, APP_NAME, "Sorry, something unexpected went wrong.")
        box.setInformativeText(str(exc) or exc_type.__name__)
        box.setDetailedText(details)
        box.exec()

    sys.excepthook = handle


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    _ensure_std_streams()
    _prepare_windows()

    from PySide6.QtCore import QCoreApplication, QSettings
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication

    from .assets import asset_path
    from .gui.main_window import MainWindow
    from .gui.theme import ThemeManager

    QCoreApplication.setOrganizationName(APP_ID)
    QCoreApplication.setApplicationName(APP_ID)
    QCoreApplication.setApplicationVersion(__version__)
    # Settings go to an .ini file in %APPDATA%\HEIC2JPEG rather than the registry.
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)

    app = QApplication(argv)
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(QIcon(asset_path("app.png")))
    font = QFont(app.font())
    font.setPointSizeF(max(font.pointSizeF(), 10.0))
    app.setFont(font)

    force_dark = {"--dark": True, "--light": False}
    theme = ThemeManager(app, next((force_dark[a] for a in argv if a in force_dark), None))
    _install_error_dialog()

    if "--selftest" in argv:
        from .selftest import run_selftest

        index = argv.index("--selftest")
        report_dir = argv[index + 1] if index + 1 < len(argv) else "."
        return run_selftest(app, theme, report_dir)

    window = MainWindow(theme)
    window.show()

    # Files dropped onto the .exe icon (or opened with "Open with") arrive as arguments.
    paths = [a for a in argv[1:] if not a.startswith("--") and os.path.exists(a)]
    if paths:
        window.add_paths(paths)

    return app.exec()
