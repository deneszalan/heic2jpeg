"""Background work: finding photos in folders, converting, and loading thumbnails."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

from ..converter import (
    ConversionError,
    ConversionOptions,
    SourceFile,
    collect_sources,
    convert_file,
    path_key,
)


class ScanWorker(QThread):
    """Expands dropped files/folders into HEIC files without freezing the window."""

    found = Signal(list, int)  # list[SourceFile], number of ignored non-HEIC files

    def __init__(self, paths: list[str], parent: QObject | None = None):
        super().__init__(parent)
        self._paths = paths

    def run(self) -> None:
        try:
            sources = collect_sources(self._paths)
            found = {path_key(s.path) for s in sources}
            ignored = sum(1 for p in self._paths if os.path.isfile(p) and path_key(p) not in found)
        except Exception:  # noqa: BLE001 - never crash the app because a folder is unreadable
            sources, ignored = [], 0
        self.found.emit(sources, ignored)


def default_worker_count() -> int:
    # Each conversion already uses several decoder threads, and big photos need a lot of
    # memory, so a handful of parallel conversions is the sweet spot.
    return max(1, min(4, os.cpu_count() or 1))


class ConversionWorker(QThread):
    """Converts a batch of photos on a few threads, reporting progress per photo."""

    item_started = Signal(str)  # key
    item_finished = Signal(str, object)  # key, ConversionResult
    item_failed = Signal(str, str)  # key, message

    def __init__(
        self,
        jobs: list[tuple[str, SourceFile]],
        options: ConversionOptions,
        parent: QObject | None = None,
        workers: int | None = None,
    ):
        super().__init__(parent)
        self._jobs = jobs
        self._options = options
        self._workers = workers or default_worker_count()
        self._cancel = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        with ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix="convert") as pool:
            for key, source in self._jobs:
                pool.submit(self._convert_one, key, source)

    def _convert_one(self, key: str, source: SourceFile) -> None:
        if self._cancel.is_set():
            return
        self.item_started.emit(key)
        try:
            result = convert_file(source, self._options)
        except ConversionError as exc:
            self.item_failed.emit(key, str(exc))
        except MemoryError:
            self.item_failed.emit(key, "Not enough memory to convert this photo.")
        except Exception as exc:  # noqa: BLE001 - report anything unexpected on the photo itself
            self.item_failed.emit(key, f"Unexpected error: {exc}")
        else:
            self.item_finished.emit(key, result)


class _ThumbnailSignals(QObject):
    ready = Signal(str, QImage)


class _ThumbnailJob(QRunnable):
    def __init__(self, key: str, path: Path, size: int, signals: _ThumbnailSignals):
        super().__init__()
        self._key = key
        self._path = path
        self._size = size
        self._signals = signals

    def run(self) -> None:
        try:
            with Image.open(self._path) as image:
                # thumbnail() lets pillow-heif use the small preview embedded in most
                # HEIC files instead of decoding the full photo.
                image.thumbnail((self._size * 3, self._size * 3))
                image = ImageOps.exif_transpose(image)
                image = ImageOps.fit(image.convert("RGBA"), (self._size, self._size), Image.Resampling.LANCZOS)
            data = image.tobytes("raw", "RGBA")
            qimage = QImage(data, self._size, self._size, self._size * 4, QImage.Format.Format_RGBA8888).copy()
        except Exception:  # noqa: BLE001 - a missing preview is not worth reporting
            qimage = QImage()
        self._signals.ready.emit(self._key, qimage)


class ThumbnailProvider(QObject):
    """Loads square photo previews in the background and caches them."""

    updated = Signal(str)  # key

    def __init__(self, logical_size: int, device_pixel_ratio: float, parent: QObject | None = None):
        super().__init__(parent)
        self._ratio = max(1.0, device_pixel_ratio)
        self._pixels = round(logical_size * self._ratio)
        self._cache: dict[str, QPixmap | None] = {}
        self._pending: set[str] = set()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._signals = _ThumbnailSignals(self)
        self._signals.ready.connect(self._on_ready)

    def get(self, key: str, path: Path) -> QPixmap | None:
        """Returns the cached preview, or None while it is loading (or if it failed)."""
        if key in self._cache:
            return self._cache[key]
        if key not in self._pending:
            self._pending.add(key)
            self._pool.start(_ThumbnailJob(key, path, self._pixels, self._signals))
        return None

    def forget(self, keys) -> None:
        for key in keys:
            self._cache.pop(key, None)

    def clear(self) -> None:
        self._pool.clear()
        self._cache.clear()
        self._pending.clear()

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(3000)

    def _on_ready(self, key: str, image: QImage) -> None:
        if key not in self._pending:
            return  # the list was cleared meanwhile
        self._pending.discard(key)
        pixmap = None
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(self._ratio)
        self._cache[key] = pixmap
        self.updated.emit(key)
