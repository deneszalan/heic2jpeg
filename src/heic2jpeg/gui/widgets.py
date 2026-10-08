"""Custom widgets: the drop zone, the drag overlay and the photo list."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .file_model import COLUMN_PHOTO, COLUMN_SIZE, COLUMN_STATUS, FileItem, ItemRole, Status, format_size
from .theme import Colors, LIGHT
from .workers import ThumbnailProvider

THUMB_SIZE = 40
ROW_HEIGHT = 58


def _paint_dashed_frame(painter: QPainter, rect: QRectF, colors: Colors, active: bool) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(colors.q("accent_soft") if active else colors.q("surface"))
    pen = QPen(colors.q("accent") if active else colors.q("border_strong"), 2)
    pen.setStyle(Qt.PenStyle.DashLine)
    pen.setDashPattern([5, 4])
    painter.setPen(pen)
    painter.drawRoundedRect(rect, 14, 14)


class DropZone(QFrame):
    """The big 'drop your photos here' area shown while the list is empty."""

    choose_files = Signal()
    choose_folder = Signal()

    def __init__(self, icon: QPixmap, parent: QWidget | None = None):
        super().__init__(parent)
        self._colors = LIGHT
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(10)
        layout.addStretch(1)

        art = QLabel()
        art.setPixmap(icon)
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(art)
        layout.addSpacing(6)

        title = QLabel("Drag and drop your HEIC photos here")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        hint = QLabel("You can also drop whole folders — the photos inside are found automatically.")
        hint.setProperty("muted", True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(8)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        files_button = QPushButton("Choose photos…")
        files_button.setObjectName("primary")
        files_button.clicked.connect(self.choose_files)
        folder_button = QPushButton("Choose a folder…")
        folder_button.setObjectName("secondaryLarge")
        folder_button.clicked.connect(self.choose_folder)
        for button in (files_button, folder_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)

    def set_colors(self, colors: Colors) -> None:
        self._colors = colors
        self.update()

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.choose_files.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        _paint_dashed_frame(painter, QRectF(self.rect()).adjusted(1, 1, -1, -1), self._colors, self._active)


class DropOverlay(QWidget):
    """Shown on top of the photo list while files are dragged over the window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._colors = LIGHT
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def set_colors(self, colors: Colors) -> None:
        self._colors = colors

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        _paint_dashed_frame(painter, rect, self._colors, True)
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() + 4)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(self._colors.q("accent"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Drop to add these photos")


class FileTableView(QTableView):
    """Photo list with whole-row hover highlighting."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.hover_row = -1
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        header.setSectionsClickable(False)
        header.setMinimumSectionSize(80)

    def setModel(self, model) -> None:
        super().setModel(model)
        header = self.horizontalHeader()
        header.setSectionResizeMode(COLUMN_PHOTO, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COLUMN_SIZE, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COLUMN_STATUS, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COLUMN_SIZE, 170)
        header.resizeSection(COLUMN_STATUS, 290)

    def _set_hover_row(self, row: int) -> None:
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()

    def mouseMoveEvent(self, event) -> None:
        self._set_hover_row(self.indexAt(event.position().toPoint()).row())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._set_hover_row(-1)
        super().leaveEvent(event)


_STATUS_STYLE = {
    Status.READY: ("Ready", "muted"),
    Status.WAITING: ("Waiting…", "muted"),
    Status.CONVERTING: ("Converting…", "accent"),
    Status.DONE: ("✓  Done", "success"),
    Status.SKIPPED: ("Skipped", "warning"),
    Status.FAILED: ("✕  Failed", "error"),
}


class FileDelegate(QStyledItemDelegate):
    """Paints each row: preview + name + folder, size, and a coloured status badge."""

    def __init__(self, thumbnails: ThumbnailProvider, parent: QWidget | None = None):
        super().__init__(parent)
        self._thumbnails = thumbnails
        self._colors = LIGHT

    def set_colors(self, colors: Colors) -> None:
        self._colors = colors

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        item: FileItem = index.data(ItemRole)
        if item is None:
            return
        c = self._colors
        view = option.widget
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, c.q("selection"))
        elif getattr(view, "hover_row", -1) == index.row():
            painter.fillRect(rect, c.q("hover"))
        painter.setPen(c.q("border"))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        column = index.column()
        if column == COLUMN_PHOTO:
            self._paint_photo(painter, rect, item, option.font)
        elif column == COLUMN_SIZE:
            self._paint_size(painter, rect, item, option.font)
        elif column == COLUMN_STATUS:
            self._paint_status(painter, rect, item, option.font)
        painter.restore()

    def _paint_photo(self, painter: QPainter, rect: QRect, item: FileItem, font: QFont) -> None:
        c = self._colors
        thumb = QRectF(rect.left() + 14, rect.top() + (rect.height() - THUMB_SIZE) / 2, THUMB_SIZE, THUMB_SIZE)
        clip = QPainterPath()
        clip.addRoundedRect(thumb, 8, 8)
        pixmap = self._thumbnails.get(item.key, item.path)
        if pixmap is not None:
            painter.save()
            painter.setClipPath(clip)
            painter.drawPixmap(thumb.toRect(), pixmap)
            painter.restore()
        else:
            painter.fillPath(clip, c.q("surface_alt"))
            painter.setPen(QPen(c.q("border"), 1))
            painter.drawPath(clip)
            small = QFont(font)
            small.setPointSizeF(max(6.0, font.pointSizeF() - 3))
            small.setWeight(QFont.Weight.DemiBold)
            painter.setFont(small)
            painter.setPen(c.q("muted"))
            painter.drawText(thumb, Qt.AlignmentFlag.AlignCenter, "HEIC")

        left = int(thumb.right()) + 12
        width = rect.right() - left - 8
        name_font = QFont(font)
        name_font.setWeight(QFont.Weight.DemiBold)
        folder_font = QFont(font)
        folder_font.setPointSizeF(max(6.0, font.pointSizeF() - 1))
        name_metrics, folder_metrics = QFontMetrics(name_font), QFontMetrics(folder_font)
        total = name_metrics.height() + 2 + folder_metrics.height()
        top = rect.top() + (rect.height() - total) // 2

        painter.setFont(name_font)
        painter.setPen(c.q("text"))
        name = name_metrics.elidedText(item.path.name, Qt.TextElideMode.ElideMiddle, width)
        painter.drawText(QRect(left, top, width, name_metrics.height()), Qt.AlignmentFlag.AlignVCenter, name)

        painter.setFont(folder_font)
        painter.setPen(c.q("muted"))
        folder = folder_metrics.elidedText(str(item.path.parent), Qt.TextElideMode.ElideMiddle, width)
        painter.drawText(
            QRect(left, top + name_metrics.height() + 2, width, folder_metrics.height()),
            Qt.AlignmentFlag.AlignVCenter,
            folder,
        )

    def _paint_size(self, painter: QPainter, rect: QRect, item: FileItem, font: QFont) -> None:
        c = self._colors
        text_rect = rect.adjusted(12, 0, -8, 0)
        painter.setFont(font)
        original = format_size(item.size)
        painter.setPen(c.q("muted"))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, original)
        if item.status is not Status.DONE:
            return
        # Draw the arrow ourselves: font arrow glyphs vary a lot between systems.
        x = text_rect.left() + QFontMetrics(font).horizontalAdvance(original) + 8
        y = text_rect.center().y() + 0.5
        painter.setPen(QPen(c.q("muted"), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(x, y), QPointF(x + 11, y))
        painter.drawLine(QPointF(x + 11, y), QPointF(x + 7, y - 4))
        painter.drawLine(QPointF(x + 11, y), QPointF(x + 7, y + 4))
        painter.setPen(c.q("text"))
        painter.drawText(
            text_rect.adjusted(int(x) - text_rect.left() + 19, 0, 0, 0),
            Qt.AlignmentFlag.AlignVCenter,
            format_size(item.output_size),
        )

    def _paint_status(self, painter: QPainter, rect: QRect, item: FileItem, font: QFont) -> None:
        text, color_name = _STATUS_STYLE[item.status]
        color = self._colors.q(color_name)
        badge_font = QFont(font)
        badge_font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(badge_font)
        height = metrics.height() + 8
        badge = QRectF(rect.left() + 12, rect.center().y() - height / 2 + 1, metrics.horizontalAdvance(text) + 22, height)

        background = QColor(color)
        background.setAlphaF(0.14 if item.status is not Status.READY else 0.10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(badge, height / 2, height / 2)
        painter.setFont(badge_font)
        painter.setPen(color)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

        if item.message and item.status in (Status.FAILED, Status.SKIPPED):
            painter.setFont(font)
            painter.setPen(self._colors.q("muted"))
            left = int(badge.right()) + 10
            note_rect = QRect(left, rect.top(), rect.right() - left - 10, rect.height())
            note = QFontMetrics(font).elidedText(item.message, Qt.TextElideMode.ElideRight, note_rect.width())
            painter.drawText(note_rect, Qt.AlignmentFlag.AlignVCenter, note)
