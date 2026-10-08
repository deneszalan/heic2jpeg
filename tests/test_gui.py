from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent

from heic2jpeg.converter import ExistingFile
from heic2jpeg.gui.file_model import Status, format_size
from heic2jpeg.gui.main_window import MainWindow, plural, quality_description
from heic2jpeg.gui.theme import ThemeManager


@pytest.fixture
def settings(tmp_path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def theme(qapp) -> ThemeManager:
    return ThemeManager(qapp, force_dark=False)


@pytest.fixture
def make_window(qtbot, theme, settings):
    def _make() -> MainWindow:
        window = MainWindow(theme, settings)
        qtbot.addWidget(window)
        return window

    return _make


def wait_for_items(qtbot, window: MainWindow, count: int) -> None:
    qtbot.waitUntil(lambda: len(window._model) == count and not window._scanners, timeout=10_000)


def wait_for_conversion(qtbot, window: MainWindow) -> None:
    qtbot.waitUntil(lambda: window._worker is None, timeout=60_000)


def test_starts_on_the_drop_zone(make_window):
    window = make_window()
    assert window._stack.currentIndex() == 0
    assert not window._convert_button.isEnabled()


def test_drop_photos_onto_the_window(qtbot, make_window, make_heic, tmp_path):
    src = make_heic(tmp_path / "dropped.heic")
    window = make_window()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(src))])
    pos = QPointF(window.rect().center())
    action, button, modifier = Qt.DropAction.CopyAction, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier

    enter = QDragEnterEvent(pos, action, mime, button, modifier)
    window.dragEnterEvent(enter)
    assert enter.isAccepted()
    assert window._drop_zone._active

    window.dropEvent(QDropEvent(pos, action, mime, button, modifier))
    wait_for_items(qtbot, window, 1)
    assert not window._drop_zone._active
    assert window._model.items[0].path == src


def test_add_folder_and_convert(qtbot, make_window, make_heic, tmp_path):
    photos = tmp_path / "photos"
    make_heic(photos / "one.heic")
    make_heic(photos / "sub" / "two.HEIC")
    window = make_window()

    window.add_paths([str(photos)])
    wait_for_items(qtbot, window, 2)

    assert window._stack.currentIndex() == 1
    assert window._convert_button.text() == "Convert 2 photos"

    assert window.start_conversion()
    assert window._convert_button.text() == "Stop"
    wait_for_conversion(qtbot, window)

    assert [item.status for item in window._model.items] == [Status.DONE, Status.DONE]
    assert (photos / "one.jpg").is_file()
    assert (photos / "sub" / "two.jpg").is_file()
    assert window._status.text().startswith("Done! 2 photos converted")
    assert not window._open_button.isHidden()
    assert window._convert_button.text() == "Convert again"


def test_failures_are_reported_per_photo(qtbot, make_window, make_heic, tmp_path):
    good = make_heic(tmp_path / "good.heic")
    bad = tmp_path / "bad.heic"
    bad.write_bytes(b"garbage")
    window = make_window()
    window.add_paths([str(good), str(bad)])
    wait_for_items(qtbot, window, 2)

    window.start_conversion()
    wait_for_conversion(qtbot, window)

    statuses = {item.path.name: item for item in window._model.items}
    assert statuses["good.heic"].status is Status.DONE
    assert statuses["bad.heic"].status is Status.FAILED
    assert "not a readable HEIC" in statuses["bad.heic"].message
    assert "1 photo could not be converted" in window._status.text()
    assert window._convert_button.text() == "Retry 1 photo"


def test_converts_into_chosen_folder(qtbot, make_window, make_heic, tmp_path):
    src = make_heic(tmp_path / "in" / "pic.heic")
    out = tmp_path / "out" / "new"
    window = make_window()
    window._folder_edit.setText(str(out))
    window._other_folder.setChecked(True)
    window._existing_combo.setCurrentIndex(window._existing_combo.findData(ExistingFile.SKIP.value))
    window._size_combo.setCurrentIndex(3)
    window._quality.setValue(70)

    options = window.options()
    assert options.output_dir == out
    assert options.existing is ExistingFile.SKIP
    assert options.max_size == 1280
    assert options.quality == 70

    window.add_paths([str(src)])
    wait_for_items(qtbot, window, 1)
    window.start_conversion()
    wait_for_conversion(qtbot, window)
    assert (out / "pic.jpg").is_file()
    assert window._last_output == out


def test_non_heic_files_are_ignored(qtbot, make_window, tmp_path):
    text_file = tmp_path / "notes.txt"
    text_file.write_text("hi")
    window = make_window()

    window.add_paths([str(text_file)])
    qtbot.waitUntil(lambda: "No HEIC photos" in window._status.text(), timeout=10_000)

    assert len(window._model) == 0
    assert "Ignored 1 file" in window._status.text()


def test_same_photo_is_only_listed_once(qtbot, make_window, make_heic, tmp_path):
    src = make_heic(tmp_path / "a.heic")
    window = make_window()
    window.add_paths([str(src)])
    wait_for_items(qtbot, window, 1)

    window.add_paths([str(src), str(tmp_path)])
    qtbot.waitUntil(lambda: "already in the list" in window._status.text(), timeout=10_000)
    assert len(window._model) == 1


def test_remove_and_clear(qtbot, make_window, make_heic, tmp_path):
    for name in ("a", "b", "c"):
        make_heic(tmp_path / f"{name}.heic")
    window = make_window()
    window.add_paths([str(tmp_path)])
    wait_for_items(qtbot, window, 3)

    window._table.selectRow(1)
    window.remove_selected()
    assert [item.path.name for item in window._model.items] == ["a.heic", "c.heic"]

    window.clear_list()
    assert len(window._model) == 0
    assert window._stack.currentIndex() == 0


def test_stop_returns_unfinished_photos_to_the_list(qtbot, make_window, make_heic, tmp_path):
    for i in range(12):
        make_heic(tmp_path / f"{i:02}.heic", size=(320, 240))
    window = make_window()
    window.add_paths([str(tmp_path)])
    wait_for_items(qtbot, window, 12)

    window.start_conversion()
    window._on_convert_clicked()  # the button reads "Stop" while converting
    wait_for_conversion(qtbot, window)

    assert window._status.text().startswith("Stopped")
    assert all(item.status in (Status.DONE, Status.READY) for item in window._model.items)


def test_settings_are_remembered(make_window, settings, tmp_path):
    window = make_window()
    window._quality.setValue(77)
    window._keep_metadata.setChecked(False)
    window._folder_edit.setText(str(tmp_path))
    window._other_folder.setChecked(True)
    window.close()

    again = make_window()
    assert again._quality.value() == 77
    assert not again._keep_metadata.isChecked()
    assert not again._remove_location.isEnabled()
    assert again.options().output_dir == Path(tmp_path)


def test_window_follows_theme_changes(make_window, theme):
    window = make_window()
    assert not window._colors.dark

    theme._force_dark = True  # what happens when Windows switches to dark mode
    theme.apply()

    assert window._colors.dark


@pytest.mark.parametrize(
    ("num_bytes", "text"),
    [(512, "512 bytes"), (2048, "2.0 KB"), (5 * 1024 * 1024, "5.0 MB"), (250 * 1024 * 1024, "250 MB")],
)
def test_format_size(num_bytes, text):
    assert format_size(num_bytes) == text


def test_helpers():
    assert plural(1) == "1 photo"
    assert plural(3) == "3 photos"
    assert quality_description(92) == "High"
    assert quality_description(100) == "Maximum"
