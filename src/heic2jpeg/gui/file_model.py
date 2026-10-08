"""The list of photos shown in the main window."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt

from ..converter import SourceFile, path_key


class Status(Enum):
    READY = "Ready"
    WAITING = "Waiting…"
    CONVERTING = "Converting…"
    DONE = "Done"
    SKIPPED = "Skipped"
    FAILED = "Failed"


@dataclass
class FileItem:
    source: SourceFile
    key: str
    size: int
    status: Status = Status.READY
    message: str = ""
    output: Path | None = None
    output_size: int = 0

    @property
    def path(self) -> Path:
        return self.source.path


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} bytes"
    size = float(num_bytes)
    for unit in ("KB", "MB", "GB"):
        size /= 1024
        if size < 1024 or unit == "GB":
            break
    return f"{size:.1f} {unit}" if size < 100 else f"{size:.0f} {unit}"


# Custom roles used by the delegate.
ItemRole = Qt.ItemDataRole.UserRole + 1

COLUMN_PHOTO, COLUMN_SIZE, COLUMN_STATUS = range(3)
HEADERS = ("Photo", "Size", "Status")


class FileTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[FileItem] = []
        self._rows: dict[str, int] = {}

    # --- Qt model API -------------------------------------------------------------
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        column = index.column()
        if role == ItemRole:
            return item
        if role == Qt.ItemDataRole.DisplayRole:
            if column == COLUMN_PHOTO:
                return item.path.name
            if column == COLUMN_SIZE:
                return format_size(item.size)
            return item.status.value
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(item, column)
        return None

    @staticmethod
    def _tooltip(item: FileItem, column: int) -> str:
        if column == COLUMN_STATUS:
            if item.status is Status.DONE and item.output:
                return f"Saved as:\n{item.output}"
            if item.status in (Status.FAILED, Status.SKIPPED):
                return item.message
            return ""
        if column == COLUMN_SIZE and item.status is Status.DONE:
            return f"Original: {format_size(item.size)}\nJPEG: {format_size(item.output_size)}"
        return str(item.path)

    # --- Item management ------------------------------------------------------------
    @property
    def items(self) -> list[FileItem]:
        return self._items

    def __len__(self) -> int:
        return len(self._items)

    def contains(self, path: Path) -> bool:
        return path_key(path) in self._rows

    def add_sources(self, sources: list[SourceFile]) -> int:
        new_items = []
        keys = set(self._rows)
        for source in sources:
            key = path_key(source.path)
            if key in keys:
                continue
            keys.add(key)
            try:
                size = source.path.stat().st_size
            except OSError:
                size = 0
            new_items.append(FileItem(source=source, key=key, size=size))
        if not new_items:
            return 0
        first = len(self._items)
        self.beginInsertRows(QModelIndex(), first, first + len(new_items) - 1)
        self._items.extend(new_items)
        self._reindex()
        self.endInsertRows()
        return len(new_items)

    def remove_rows(self, rows: list[int]) -> None:
        rows = sorted({r for r in rows if 0 <= r < len(self._items)}, reverse=True)
        # Remove contiguous blocks at once (bottom-up so earlier row numbers stay valid).
        while rows:
            last = first = rows.pop(0)
            while rows and rows[0] == first - 1:
                first = rows.pop(0)
            self.beginRemoveRows(QModelIndex(), first, last)
            del self._items[first : last + 1]
            self.endRemoveRows()
        self._reindex()

    def clear(self) -> None:
        self.beginResetModel()
        self._items.clear()
        self._rows.clear()
        self.endResetModel()

    def item(self, row: int) -> FileItem:
        return self._items[row]

    def item_by_key(self, key: str) -> FileItem | None:
        row = self._rows.get(key)
        return None if row is None else self._items[row]

    def update(self, key: str, **changes) -> None:
        row = self._rows.get(key)
        if row is None:
            return
        item = self._items[row]
        for name, value in changes.items():
            setattr(item, name, value)
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(HEADERS) - 1))

    def refresh_row(self, key: str) -> None:
        row = self._rows.get(key)
        if row is not None:
            self.dataChanged.emit(self.index(row, COLUMN_PHOTO), self.index(row, COLUMN_PHOTO))

    def refresh_all(self) -> None:
        if self._items:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._items) - 1, len(HEADERS) - 1))

    def count(self, *statuses: Status) -> int:
        return sum(1 for item in self._items if item.status in statuses)

    def _reindex(self) -> None:
        self._rows = {item.key: row for row, item in enumerate(self._items)}
