"""End-to-end check of the packaged app, run by the build pipeline: HEIC2JPEG.exe --selftest <dir>.

It creates sample photos, drives the real window through a conversion, verifies the
JPEGs and saves screenshots plus a report into <dir>. Exit code 0 means success.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import traceback
from pathlib import Path

from PIL import ExifTags, Image, ImageDraw
from PySide6.QtCore import QEventLoop, QSettings
from PySide6.QtWidgets import QApplication

from . import __version__
from .converter import HEIC_EXTENSIONS  # noqa: F401 - also registers the HEIF plugin
from .gui.file_model import Status
from .gui.main_window import MainWindow
from .gui.theme import ThemeManager

SAMPLES = [
    ("IMG_4021.HEIC", (64, 140, 230), (255, 190, 70), (40, 120, 80)),
    ("IMG_4022.HEIC", (250, 140, 90), (255, 230, 120), (120, 60, 110)),
    ("IMG_4023.HEIC", (110, 200, 235), (255, 255, 255), (60, 150, 90)),
    ("IMG_4024.HEIC", (30, 40, 90), (240, 240, 200), (20, 30, 60)),
    ("IMG_4025.HEIC", (255, 170, 190), (255, 240, 220), (200, 90, 120)),
]


def _sample_photo(size, sky, sun, hills) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, sky)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / height
        draw.line([(0, y), (width, y)], fill=tuple(int(c * (1 - 0.35 * t)) for c in sky))
    r = width // 9
    draw.ellipse((width * 0.68 - r, height * 0.28 - r, width * 0.68 + r, height * 0.28 + r), fill=sun)
    draw.polygon([(0, height), (width * 0.35, height * 0.45), (width * 0.75, height)], fill=hills)
    darker = tuple(int(c * 0.7) for c in hills)
    draw.polygon([(width * 0.4, height), (width * 0.75, height * 0.55), (width, height * 0.8), (width, height)], fill=darker)
    return image


def _wait_for(app: QApplication, condition, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError(f"Timed out waiting for {what}")
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


def _settle(app: QApplication, seconds: float = 0.5) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)


def run_selftest(app: QApplication, theme: ThemeManager, report_dir: str) -> int:
    report = Path(report_dir)
    report.mkdir(parents=True, exist_ok=True)
    log: list[str] = [f"HEIC to JPEG Converter {__version__} self-test"]
    work = Path(tempfile.mkdtemp(prefix="heic2jpeg-selftest-"))
    try:
        import pillow_heif

        log.append(f"libheif: {pillow_heif.libheif_info()}")
        photos = work / "Camera Roll"
        photos.mkdir()
        exif = Image.Exif()
        exif[ExifTags.Base.Make] = "Apple"
        exif[ExifTags.Base.DateTime] = "2024:07:14 18:30:00"
        for name, *colors in SAMPLES:
            _sample_photo((1200, 900), *colors).save(photos / name, format="HEIF", quality=80, exif=exif.tobytes())
        (photos / "IMG_4026.HEIC").write_bytes(b"not really a photo")
        log.append(f"Created {len(SAMPLES) + 1} sample files in {photos}")

        settings = QSettings(str(work / "settings.ini"), QSettings.Format.IniFormat)
        window = MainWindow(theme, settings)
        window.resize(1000, 740)
        window.show()
        _settle(app)
        window.grab().save(str(report / "screenshot-empty.png"))

        window.add_paths([str(photos)])
        _wait_for(app, lambda: len(window._model) == len(SAMPLES) + 1, 30, "photos to be listed")
        _wait_for(
            app,
            lambda: all(window._thumbnails.get(i.key, i.path) is not None for i in window._model.items[: len(SAMPLES)]),
            30,
            "thumbnails",
        )
        _settle(app)
        window.grab().save(str(report / "screenshot-list.png"))

        if not window.start_conversion():
            raise RuntimeError("Conversion did not start")
        _wait_for(app, lambda: window._worker is None, 120, "the conversion to finish")
        _settle(app)
        window.grab().save(str(report / "screenshot-done.png"))

        statuses = {item.path.name: item.status for item in window._model.items}
        log.append(f"Statuses: { {k: v.name for k, v in statuses.items()} }")
        expected = {name: Status.DONE for name, *_ in SAMPLES} | {"IMG_4026.HEIC": Status.FAILED}
        if statuses != expected:
            raise AssertionError(f"Unexpected statuses: {statuses}")
        for name, *_ in SAMPLES:
            jpeg = photos / (Path(name).stem + ".jpg")
            with Image.open(jpeg) as image:
                if image.format != "JPEG" or image.size != (1200, 900):
                    raise AssertionError(f"{jpeg} is not the expected JPEG: {image.format} {image.size}")
                if image.getexif().get(ExifTags.Base.Make) != "Apple":
                    raise AssertionError(f"{jpeg} lost its EXIF data")
        log.append("Status text: " + window._status.text())
        log.append("OK")
        window.close()
        return 0
    except Exception:  # noqa: BLE001
        log.append(traceback.format_exc())
        log.append("FAILED")
        return 1
    finally:
        (report / "selftest.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)
