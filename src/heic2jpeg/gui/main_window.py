"""The application window."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QSettings, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME
from ..assets import asset_path
from ..converter import ConversionOptions, ConversionResult, ExistingFile, Outcome
from .file_model import FileTableModel, Status, format_size
from .theme import Colors, ThemeManager
from .widgets import THUMB_SIZE, DropOverlay, DropZone, FileDelegate, FileTableView
from .workers import ConversionWorker, ScanWorker, ThumbnailProvider

SIZE_CHOICES = [
    ("Original size", None),
    ("Large — 3840 px", 3840),
    ("Medium — 2048 px", 2048),
    ("Small — 1280 px (for e-mail)", 1280),
]

EXISTING_CHOICES = [
    ("Keep both (add a number)", ExistingFile.RENAME),
    ("Replace the old JPEG", ExistingFile.OVERWRITE),
    ("Skip that photo", ExistingFile.SKIP),
]

DEFAULT_QUALITY = 92


def plural(count: int, word: str = "photo") -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def quality_description(value: int) -> str:
    if value >= 96:
        return "Maximum"
    if value >= 88:
        return "High"
    if value >= 75:
        return "Good"
    if value >= 60:
        return "Medium"
    return "Small file"


def format_duration(ms: int) -> str:
    seconds = ms / 1000
    if seconds < 10:
        return f"{seconds:.1f} seconds"
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    return f"{seconds / 60:.0f} minutes"


def show_in_file_manager(path: Path) -> None:
    if sys.platform == "win32" and path.exists():
        subprocess.Popen(f'explorer /select,"{path}"')
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


def open_with_default_app(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class MainWindow(QMainWindow):
    def __init__(self, theme: ThemeManager, settings: QSettings | None = None):
        super().__init__()
        self._theme = theme
        self._settings = settings or QSettings()
        self._model = FileTableModel(self)
        self._thumbnails = ThumbnailProvider(THUMB_SIZE, self.devicePixelRatioF(), self)
        self._worker: ConversionWorker | None = None
        self._scanners: set[ScanWorker] = set()
        self._batch_keys: list[str] = []
        self._timer = QElapsedTimer()
        self._last_output: Path | None = None
        self._batch_output_dir: Path | None = None
        self._colors = theme.colors
        self._status_kind = "muted"
        self._status_is_custom = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(asset_path("app.png")))
        self.setAcceptDrops(True)
        self.setMinimumSize(900, 660)
        self.resize(1040, 780)

        self._build_ui()
        self._load_settings()
        self._connect_signals()
        self._apply_colors(theme.colors)
        theme.changed.connect(self._apply_colors)
        self._update_state()

    # ------------------------------------------------------------------ UI building
    def _build_ui(self) -> None:
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        layout.addLayout(self._build_header())

        self._stack = QStackedWidget()
        self._drop_zone = DropZone(QIcon(asset_path("app.png")).pixmap(84, 84))
        self._stack.addWidget(self._drop_zone)
        self._stack.addWidget(self._build_list_card())
        layout.addWidget(self._stack, 1)

        layout.addWidget(self._build_options_card())
        layout.addLayout(self._build_bottom_bar())

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(QIcon(asset_path("app.png")).pixmap(48, 48))
        header.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("HEIC to JPEG Converter", objectName="title")
        subtitle = QLabel("Turn iPhone photos (.heic) into JPEG pictures that open everywhere.")
        subtitle.setProperty("muted", True)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        return header

    def _build_list_card(self) -> QFrame:
        card = QFrame(objectName="card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(1, 0, 1, 10)
        layout.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(16, 10, 12, 10)
        toolbar.setSpacing(6)
        self._summary = QLabel(objectName="summary")
        toolbar.addWidget(self._summary)
        toolbar.addStretch(1)
        self._add_files_button = QPushButton("Add photos…", objectName="ghost")
        self._add_folder_button = QPushButton("Add folder…", objectName="ghost")
        self._remove_button = QPushButton("Remove", objectName="ghost")
        self._clear_button = QPushButton("Clear list", objectName="ghost")
        self._remove_button.setToolTip("Remove the selected photos from the list (Delete)")
        self._clear_button.setToolTip("Remove all photos from the list. Your files are not touched.")
        for button in (self._add_files_button, self._add_folder_button, self._remove_button, self._clear_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        self._table = FileTableView()
        self._table.setModel(self._model)
        self._delegate = FileDelegate(self._thumbnails, self._table)
        self._table.setItemDelegate(self._delegate)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self._table, 1)

        self._overlay = DropOverlay(card)
        self._list_card = card
        return card

    def _build_options_card(self) -> QFrame:
        card = QFrame(objectName="card")
        grid = QGridLayout(card)
        grid.setContentsMargins(20, 16, 20, 16)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setColumnMinimumWidth(2, 110)

        def label(text: str) -> QLabel:
            widget = QLabel(text, objectName="sectionLabel")
            return widget

        # Row 0: where to save.
        grid.addWidget(label("Save to"), 0, 0)
        save_row = QHBoxLayout()
        save_row.setSpacing(12)
        self._same_folder = QRadioButton("Same folder as the original photos")
        self._other_folder = QRadioButton("This folder:")
        self._folder_group = QButtonGroup(self)
        self._folder_group.addButton(self._same_folder)
        self._folder_group.addButton(self._other_folder)
        self._folder_edit = QLineEdit()
        self._folder_edit.setReadOnly(True)
        self._folder_edit.setPlaceholderText("Choose a folder…")
        self._browse_button = QPushButton("Browse…")
        self._browse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        save_row.addWidget(self._same_folder)
        save_row.addSpacing(8)
        save_row.addWidget(self._other_folder)
        save_row.addWidget(self._folder_edit, 1)
        save_row.addWidget(self._browse_button)
        grid.addLayout(save_row, 0, 1, 1, 4)

        # Row 1: quality and size.
        grid.addWidget(label("Quality"), 1, 0)
        quality_row = QHBoxLayout()
        quality_row.setSpacing(12)
        self._quality = QSlider(Qt.Orientation.Horizontal)
        self._quality.setRange(50, 100)
        self._quality.setPageStep(5)
        self._quality.setCursor(Qt.CursorShape.PointingHandCursor)
        self._quality.setToolTip("Higher quality means sharper photos but bigger files. 90–95 is a good choice.")
        self._quality_label = QLabel()
        self._quality_label.setMinimumWidth(110)
        quality_row.addWidget(self._quality, 1)
        quality_row.addWidget(self._quality_label)
        grid.addLayout(quality_row, 1, 1)

        grid.addWidget(label("Photo size"), 1, 2)
        self._size_combo = QComboBox()
        for text, value in SIZE_CHOICES:
            self._size_combo.addItem(text, value)
        self._size_combo.setToolTip("Make photos smaller, e.g. for sending by e-mail. Sizes refer to the longest side.")
        grid.addWidget(self._size_combo, 1, 3, 1, 2)

        # Row 2: existing files and photo details.
        grid.addWidget(label("If the JPEG exists"), 2, 0)
        self._existing_combo = QComboBox()
        for text, value in EXISTING_CHOICES:
            self._existing_combo.addItem(text, value.value)
        self._existing_combo.setToolTip("What to do when a JPEG with the same name is already in the folder.")
        grid.addWidget(self._existing_combo, 2, 1)

        grid.addWidget(label("Details"), 2, 2)
        self._keep_metadata = QCheckBox("Keep date taken and camera info")
        self._keep_metadata.setToolTip("Copies the EXIF information (date taken, camera, lens…) into the JPEG.")
        grid.addWidget(self._keep_metadata, 2, 3, 1, 2)

        # Row 3: dates and privacy.
        self._keep_dates = QCheckBox("Keep the original file dates")
        self._keep_dates.setToolTip("The JPEG gets the same 'Date modified' as the original, so it sorts the same way.")
        grid.addWidget(self._keep_dates, 3, 1)
        self._remove_location = QCheckBox("Remove location (GPS) for privacy")
        self._remove_location.setToolTip("Leaves out where the photo was taken. Useful before sharing photos.")
        grid.addWidget(self._remove_location, 3, 3, 1, 2)

        self._options_card = card
        return card

    def _build_bottom_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(12)
        status_column = QVBoxLayout()
        status_column.setSpacing(8)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.hide()
        status_column.addStretch(1)
        status_column.addWidget(self._status)
        status_column.addWidget(self._progress)
        status_column.addStretch(1)
        bar.addLayout(status_column, 1)

        self._open_button = QPushButton("Open folder")
        self._open_button.setObjectName("secondaryLarge")
        self._open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_button.setToolTip("Show the converted JPEGs in File Explorer")
        self._open_button.hide()
        bar.addWidget(self._open_button)

        self._convert_button = QPushButton("Convert", objectName="primary")
        self._convert_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._convert_button.setMinimumWidth(220)
        self._convert_button.setDefault(True)
        bar.addWidget(self._convert_button)
        return bar

    def _connect_signals(self) -> None:
        self._drop_zone.choose_files.connect(self.choose_files)
        self._drop_zone.choose_folder.connect(self.choose_folder)
        self._add_files_button.clicked.connect(self.choose_files)
        self._add_folder_button.clicked.connect(self.choose_folder)
        self._remove_button.clicked.connect(self.remove_selected)
        self._clear_button.clicked.connect(self.clear_list)
        self._convert_button.clicked.connect(self._on_convert_clicked)
        self._open_button.clicked.connect(self.open_output_folder)
        self._browse_button.clicked.connect(self._browse_output_folder)
        self._other_folder.toggled.connect(self._on_other_folder_toggled)
        self._quality.valueChanged.connect(self._update_quality_label)
        self._folder_edit.textChanged.connect(self._folder_edit.setToolTip)
        self._keep_metadata.toggled.connect(self._remove_location.setEnabled)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.doubleClicked.connect(lambda index: self._open_item(index.row()))
        self._table.selectionModel().selectionChanged.connect(lambda *_: self._update_state())
        self._thumbnails.updated.connect(self._model.refresh_row)
        self._model.rowsInserted.connect(lambda *_: self._update_state())
        self._model.rowsRemoved.connect(lambda *_: self._update_state())
        self._model.modelReset.connect(lambda *_: self._update_state())

        QShortcut(QKeySequence.StandardKey.Delete, self._table, activated=self.remove_selected)
        QShortcut(QKeySequence.StandardKey.Open, self, activated=self.choose_files)

    # ------------------------------------------------------------------ settings
    def _load_settings(self) -> None:
        s = self._settings
        geometry = s.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self._quality.setValue(int(s.value("quality", DEFAULT_QUALITY)))
        self._update_quality_label(self._quality.value())
        self._size_combo.setCurrentIndex(min(int(s.value("sizeIndex", 0)), self._size_combo.count() - 1))
        existing = s.value("existing", ExistingFile.RENAME.value)
        index = self._existing_combo.findData(existing)
        self._existing_combo.setCurrentIndex(max(index, 0))
        self._keep_metadata.setChecked(self._bool(s.value("keepMetadata", True)))
        self._remove_location.setChecked(self._bool(s.value("removeLocation", False)))
        self._remove_location.setEnabled(self._keep_metadata.isChecked())
        self._keep_dates.setChecked(self._bool(s.value("keepFileDates", True)))
        folder = str(s.value("outputFolder", "") or "")
        self._folder_edit.setText(folder)
        self._folder_edit.setToolTip(folder)
        use_other = self._bool(s.value("useOutputFolder", False)) and bool(folder)
        (self._other_folder if use_other else self._same_folder).setChecked(True)

    def _save_settings(self) -> None:
        s = self._settings
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("quality", self._quality.value())
        s.setValue("sizeIndex", self._size_combo.currentIndex())
        s.setValue("existing", self._existing_combo.currentData())
        s.setValue("keepMetadata", self._keep_metadata.isChecked())
        s.setValue("removeLocation", self._remove_location.isChecked())
        s.setValue("keepFileDates", self._keep_dates.isChecked())
        s.setValue("outputFolder", self._folder_edit.text())
        s.setValue("useOutputFolder", self._other_folder.isChecked())
        s.sync()

    @staticmethod
    def _bool(value) -> bool:
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes")
        return bool(value)

    def options(self) -> ConversionOptions:
        folder = self._folder_edit.text().strip()
        return ConversionOptions(
            quality=self._quality.value(),
            output_dir=Path(folder) if self._other_folder.isChecked() and folder else None,
            keep_metadata=self._keep_metadata.isChecked(),
            remove_location=self._remove_location.isChecked(),
            max_size=self._size_combo.currentData(),
            existing=ExistingFile(self._existing_combo.currentData()),
            keep_file_dates=self._keep_dates.isChecked(),
        )

    # ------------------------------------------------------------------ theme
    def _apply_colors(self, colors: Colors) -> None:
        self._colors = colors
        self._drop_zone.set_colors(colors)
        self._overlay.set_colors(colors)
        self._delegate.set_colors(colors)
        self._apply_status_style()
        self._table.viewport().update()

    def _apply_status_style(self) -> None:
        color = getattr(self._colors, self._status_kind)
        weight = 400 if self._status_kind == "muted" else 600
        self._status.setStyleSheet(f"color: {color}; font-weight: {weight};")

    def _set_status(self, text: str, kind: str = "muted", custom: bool = True) -> None:
        self._status.setText(text)
        self._status_kind = kind
        self._status_is_custom = custom
        self._apply_status_style()

    # ------------------------------------------------------------------ adding photos
    def _last_folder(self) -> str:
        folder = str(self._settings.value("lastOpenFolder", "") or "")
        if folder and Path(folder).is_dir():
            return folder
        return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)

    def choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Choose HEIC photos",
            self._last_folder(),
            "HEIC photos (*.heic *.heif *.hif);;All files (*)",
        )
        if files:
            self._settings.setValue("lastOpenFolder", str(Path(files[0]).parent))
            self.add_paths(files)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder with HEIC photos", self._last_folder())
        if folder:
            self._settings.setValue("lastOpenFolder", folder)
            self.add_paths([folder])

    def add_paths(self, paths: list[str]) -> None:
        """Add files and/or folders. Folders are searched in the background."""
        if not paths:
            return
        scanner = ScanWorker(list(paths), self)
        self._scanners.add(scanner)
        scanner.found.connect(self._on_scan_finished)
        scanner.finished.connect(lambda: self._scanners.discard(scanner))
        scanner.finished.connect(scanner.deleteLater)
        self._set_status("Looking for photos…", "muted")
        scanner.start()

    def _on_scan_finished(self, sources: list, ignored: int) -> None:
        added = self._model.add_sources(sources)
        already = len(sources) - added
        parts = []
        if added:
            parts.append(f"Added {plural(added)}.")
        elif already:
            parts.append("Those photos are already in the list.")
        else:
            parts.append("No HEIC photos were found in what you added.")
        if ignored:
            parts.append(f"Ignored {plural(ignored, 'file')} that {'is' if ignored == 1 else 'are'} not HEIC.")
        if added and not self._worker:
            parts.append("Press Convert when you are ready.")
        self._set_status(" ".join(parts), "muted" if added else "warning")
        if added:
            self._open_button.hide()
        self._update_state()

    def remove_selected(self) -> None:
        if self._worker:
            return
        rows = [index.row() for index in self._table.selectionModel().selectedRows()]
        if not rows:
            return
        self._thumbnails.forget(self._model.item(row).key for row in rows)
        self._model.remove_rows(rows)
        self._set_status("", custom=False)
        self._update_state()

    def clear_list(self) -> None:
        if self._worker:
            return
        self._model.clear()
        self._thumbnails.clear()
        self._open_button.hide()
        self._set_status("", custom=False)
        self._update_state()

    # ------------------------------------------------------------------ output folder
    def _browse_output_folder(self) -> bool:
        start = self._folder_edit.text() or self._last_folder()
        folder = QFileDialog.getExistingDirectory(self, "Where should the JPEGs be saved?", start)
        if folder:
            self._folder_edit.setText(str(Path(folder)))
            self._other_folder.setChecked(True)
            return True
        return False

    def _on_other_folder_toggled(self, checked: bool) -> None:
        if checked and not self._folder_edit.text() and not self._browse_output_folder():
            self._same_folder.setChecked(True)
        self._update_state()

    def open_output_folder(self) -> None:
        if self._last_output is None:
            return
        if self._last_output.is_dir():
            open_with_default_app(self._last_output)
        else:
            show_in_file_manager(self._last_output)

    # ------------------------------------------------------------------ conversion
    def _on_convert_clicked(self) -> None:
        if self._worker:
            self._worker.cancel()
            self._convert_button.setEnabled(False)
            self._set_status("Stopping after the photos in progress…", "muted")
            return
        self.start_conversion()

    def start_conversion(self) -> bool:
        if self._scanners:
            self._set_status("Still looking for photos — one moment…", "muted")
            return False
        items = [i for i in self._model.items if i.status in (Status.READY, Status.FAILED)]
        if not items and len(self._model):
            answer = QMessageBox.question(
                self,
                "Convert again?",
                "All photos in the list have already been converted.\n\nConvert them again with the current settings?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
            items = list(self._model.items)
        if not items:
            return False

        options = self.options()
        if options.output_dir is not None:
            try:
                options.output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                QMessageBox.warning(
                    self,
                    "Cannot use this folder",
                    f"The folder\n{options.output_dir}\ncannot be used:\n{exc.strerror or exc}\n\n"
                    "Please choose another folder.",
                )
                return False

        self._save_settings()
        self._batch_keys = [item.key for item in items]
        for item in items:
            self._model.update(item.key, status=Status.WAITING, message="", output=None, output_size=0)

        self._worker = ConversionWorker([(item.key, item.source) for item in items], options, self)
        self._worker.item_started.connect(self._on_item_started)
        self._worker.item_finished.connect(self._on_item_finished)
        self._worker.item_failed.connect(self._on_item_failed)
        self._worker.finished.connect(self._on_batch_finished)
        self._batch_output_dir = options.output_dir

        self._progress.setRange(0, len(items))
        self._progress.setValue(0)
        self._progress.show()
        self._open_button.hide()
        self._timer.start()
        self._update_progress_text()
        self._update_state()
        self._worker.start()
        return True

    def _on_item_started(self, key: str) -> None:
        self._model.update(key, status=Status.CONVERTING)

    def _on_item_finished(self, key: str, result: ConversionResult) -> None:
        if result.outcome is Outcome.SKIPPED:
            self._model.update(key, status=Status.SKIPPED, message="A JPEG with this name already exists", output=result.output)
        else:
            self._model.update(key, status=Status.DONE, output=result.output, output_size=result.output_size)
        self._advance()

    def _on_item_failed(self, key: str, message: str) -> None:
        self._model.update(key, status=Status.FAILED, message=message)
        self._advance()

    def _advance(self) -> None:
        self._progress.setValue(self._progress.value() + 1)
        if self._worker and not self._worker.cancelled:
            self._update_progress_text()

    def _update_progress_text(self) -> None:
        done = self._progress.value()
        total = self._progress.maximum()
        self._set_status(f"Converting {min(done + 1, total)} of {plural(total)}…", "muted")

    def _on_batch_finished(self) -> None:
        worker, self._worker = self._worker, None
        cancelled = worker.cancelled if worker else False
        if worker:
            worker.deleteLater()

        batch = [item for key in self._batch_keys if (item := self._model.item_by_key(key))]
        for item in batch:
            if item.status in (Status.WAITING, Status.CONVERTING):
                self._model.update(item.key, status=Status.READY)
        converted = [i for i in batch if i.status is Status.DONE]
        skipped = sum(1 for i in batch if i.status is Status.SKIPPED)
        failed = sum(1 for i in batch if i.status is Status.FAILED)
        elapsed = format_duration(self._timer.elapsed())

        if cancelled:
            text = f"Stopped. {plural(len(converted))} converted, the rest are still in the list."
            kind = "warning"
        elif converted:
            text = f"Done! {plural(len(converted))} converted in {elapsed}."
            kind = "success"
        else:
            text = "Nothing was converted."
            kind = "warning"
        if skipped:
            text += f" {plural(skipped)} skipped because the JPEG already existed."
        if failed:
            text += f" {plural(failed)} could not be converted — hover over {'it' if failed == 1 else 'them'} to see why."
            kind = "warning" if converted else "error"
        self._set_status(text, kind)
        self._progress.hide()

        self._last_output = self._output_location(converted)
        self._open_button.setVisible(self._last_output is not None)
        self._convert_button.setEnabled(True)
        self._update_state()
        QApplication.alert(self)

    def _output_location(self, converted) -> Path | None:
        if not converted:
            return None
        if self._batch_output_dir is not None:
            return self._batch_output_dir
        folders = {i.output.parent for i in converted if i.output}
        if len(folders) == 1:
            return folders.pop()
        return converted[0].output

    # ------------------------------------------------------------------ list interactions
    def _open_item(self, row: int) -> None:
        if not (0 <= row < len(self._model)):
            return
        item = self._model.item(row)
        target = item.output if item.status is Status.DONE and item.output else item.path
        open_with_default_app(target)

    def _show_context_menu(self, pos) -> None:
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        if not self._table.selectionModel().isRowSelected(index.row()):
            self._table.selectRow(index.row())
        item = self._model.item(index.row())
        menu = QMenu(self)
        if item.status is Status.DONE and item.output:
            menu.addAction("Open JPEG", lambda: open_with_default_app(item.output))
            menu.addAction("Show JPEG in folder", lambda: show_in_file_manager(item.output))
            menu.addSeparator()
        menu.addAction("Open original photo", lambda: open_with_default_app(item.path))
        menu.addAction("Show original in folder", lambda: show_in_file_manager(item.path))
        menu.addSeparator()
        remove: QAction = menu.addAction("Remove from list", self.remove_selected)
        remove.setEnabled(self._worker is None)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ state
    def _update_quality_label(self, value: int) -> None:
        self._quality_label.setText(f"{value}  ·  {quality_description(value)}")

    def _update_state(self) -> None:
        count = len(self._model)
        running = self._worker is not None
        self._stack.setCurrentIndex(1 if count else 0)

        total_size = sum(item.size for item in self._model.items)
        self._summary.setText(f"{plural(count)}  ·  {format_size(total_size)}" if count else "")

        ready = self._model.count(Status.READY)
        failed = self._model.count(Status.FAILED)
        todo = ready + failed
        if running:
            self._convert_button.setText("Stop")
            self._convert_button.setObjectName("stop")
        else:
            self._convert_button.setObjectName("primary")
            if ready:
                self._convert_button.setText(f"Convert {plural(todo)}")
            elif failed:
                self._convert_button.setText(f"Retry {plural(failed)}")
            elif count:
                self._convert_button.setText("Convert again")
            else:
                self._convert_button.setText("Convert")
            self._convert_button.setEnabled(count > 0)
        self._convert_button.style().unpolish(self._convert_button)
        self._convert_button.style().polish(self._convert_button)

        has_selection = self._table.selectionModel().hasSelection()
        self._remove_button.setEnabled(not running and has_selection)
        self._clear_button.setEnabled(not running and count > 0)
        self._options_card.setEnabled(not running)
        self._folder_edit.setEnabled(self._other_folder.isChecked())

        if not self._status_is_custom and not running:
            if count:
                self._set_status(f"Ready to convert {plural(todo)}." if todo else "", "muted", custom=False)
            else:
                self._set_status("Add some photos to get started.", "muted", custom=False)

    # ------------------------------------------------------------------ drag & drop
    def _set_drop_highlight(self, on: bool) -> None:
        self._drop_zone.set_active(on)
        if on and self._stack.currentIndex() == 1:
            self._overlay.setGeometry(self._list_card.rect())
            self._overlay.raise_()
            self._overlay.show()
        else:
            self._overlay.hide()

    @staticmethod
    def _local_paths(event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]

    def dragEnterEvent(self, event) -> None:
        if self._local_paths(event):
            event.acceptProposedAction()
            self._set_drop_highlight(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._set_drop_highlight(False)

    def dropEvent(self, event) -> None:
        self._set_drop_highlight(False)
        paths = self._local_paths(event)
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    # ------------------------------------------------------------------ closing
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker:
            answer = QMessageBox.question(
                self,
                "Conversion in progress",
                "Photos are still being converted.\n\nStop and close the program?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._worker.cancel()
            self._worker.wait()
        for scanner in list(self._scanners):
            scanner.wait(3000)
        self._save_settings()
        self._thumbnails.shutdown()
        event.accept()
