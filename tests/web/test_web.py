"""End-to-end tests of the web version in a real (headless) Chromium.

They serve web/ locally, feed it HEIC files through the page's own controls,
and check the JPEGs it downloads. Requires: pip install playwright && playwright install chromium
(set CHROMIUM_PATH to use an existing Chromium instead).
"""

from __future__ import annotations

import base64
import functools
import io
import os
import threading
import zipfile
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import ExifTags, Image

playwright_api = pytest.importorskip("playwright.sync_api")

from ..conftest import build_exif  # noqa: E402

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(WEB_DIR)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        yield browser
        browser.close()


@pytest.fixture
def page(browser, site_url):
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(site_url)
    yield page
    context.close()
    assert not errors, f"JavaScript errors: {errors}"


def wait_for_status(page, prefix: str, timeout: float = 60_000) -> str:
    page.wait_for_function(
        "prefix => document.getElementById('status').textContent.startsWith(prefix)", arg=prefix, timeout=timeout
    )
    return page.text_content("#status")


def convert(page) -> str:
    page.click("#convert")
    page.wait_for_function("() => !window.heic2jpeg.run.active", timeout=120_000)
    return page.text_content("#status")


def download(page, folder: Path) -> Path:
    with page.expect_download() as info:
        page.click("#download")
    target = folder / info.value.suggested_filename
    info.value.save_as(target)
    return target


def jpegs_in_zip(path: Path) -> dict[str, Image.Image]:
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None  # all CRCs match
        return {name: Image.open(io.BytesIO(archive.read(name))) for name in archive.namelist()}


def test_page_starts_empty(page):
    assert page.is_visible("#drop-zone")
    assert page.is_disabled("#convert")
    assert page.text_content("#status") == "Add some photos to get started."


def test_converts_photos_keeping_details(page, tmp_path, make_heic, srgb_icc):
    photos = [
        make_heic(tmp_path / "in" / "IMG_0001.HEIC", size=(320, 240), exif=build_exif(), icc=srgb_icc),
        make_heic(tmp_path / "in" / "IMG_0002.heic", size=(320, 192), exif=build_exif(orientation=6)),
    ]
    page.set_input_files("#file-input", [str(p) for p in photos])
    wait_for_status(page, "Added 2 photos.")
    assert page.text_content("#convert") == "Convert 2 photos"

    status = convert(page)
    assert status.startswith("Done! 2 photos converted"), status
    assert page.locator(".badge.done").count() == 2
    assert page.text_content("#download") == "Download all (ZIP)"

    zip_path = download(page, tmp_path)
    jpegs = jpegs_in_zip(zip_path)
    assert sorted(jpegs) == ["IMG_0001.jpg", "IMG_0002.jpg"]

    first = jpegs["IMG_0001.jpg"]
    assert first.format == "JPEG" and first.size == (320, 240)
    r, g, b = first.convert("RGB").getpixel((160, 120))
    assert abs(r - 200) < 14 and abs(g - 60) < 14 and abs(b - 40) < 14
    exif = first.getexif()
    assert exif[ExifTags.Base.Make] == "Apple"
    assert exif[ExifTags.Base.DateTime] == "2024:07:14 18:30:00"
    assert exif.get_ifd(ExifTags.IFD.GPSInfo)[ExifTags.GPS.GPSLatitudeRef] == "N"
    assert first.info.get("icc_profile") == srgb_icc

    rotated = jpegs["IMG_0002.jpg"]
    assert rotated.size == (192, 320)  # portrait photo stays upright
    assert rotated.getexif().get(ExifTags.Base.Orientation) == 1


def test_remove_location(page, tmp_path, make_heic):
    src = make_heic(tmp_path / "trip.heic", size=(400, 200), exif=build_exif())
    page.check("#remove-location")
    page.set_input_files("#file-input", str(src))
    wait_for_status(page, "Added 1 photo.")
    convert(page)

    jpeg_path = download(page, tmp_path)
    assert jpeg_path.name == "trip.jpg"
    with Image.open(jpeg_path) as jpeg:
        exif = jpeg.getexif()
        assert exif[ExifTags.Base.Make] == "Apple"
        assert ExifTags.IFD.GPSInfo not in exif
    # The coordinates (47° 29') must be wiped from the file, not just unlinked.
    raw = jpeg_path.read_bytes()
    for order in ("big", "little"):
        assert (47).to_bytes(4, order) + (1).to_bytes(4, order) not in raw
        assert (29).to_bytes(4, order) + (1).to_bytes(4, order) not in raw


def test_resize_and_quality(page, tmp_path):
    src = tmp_path / "big.heic"
    Image.effect_noise((1600, 800), 50).convert("RGB").save(src, format="HEIF", quality=90)
    page.set_input_files("#file-input", str(src))
    wait_for_status(page, "Added 1 photo.")
    page.select_option("#size", "1280")
    page.fill("#quality", "95")
    convert(page)
    sharp = download(page, tmp_path / "sharp")
    with Image.open(sharp) as jpeg:
        assert jpeg.size == (1280, 640)

    page.fill("#quality", "50")
    page.once("dialog", lambda dialog: dialog.accept())  # "Convert them again?"
    assert page.text_content("#convert") == "Convert again"
    convert(page)
    small = download(page, tmp_path / "small")
    assert small.stat().st_size < sharp.stat().st_size


def test_transparent_photo_gets_white_background(page, tmp_path, make_heic):
    src = make_heic(tmp_path / "alpha.heic", mode="RGBA", color=(0, 0, 0, 0))
    page.set_input_files("#file-input", str(src))
    wait_for_status(page, "Added 1 photo.")
    convert(page)
    with Image.open(download(page, tmp_path)) as jpeg:
        assert all(channel > 240 for channel in jpeg.convert("RGB").getpixel((10, 10)))


def test_damaged_file_is_reported(page, tmp_path, make_heic):
    good = make_heic(tmp_path / "good.heic")
    bad = tmp_path / "bad.heic"
    bad.write_bytes(b"definitely not a photo")
    page.set_input_files("#file-input", [str(good), str(bad)])
    wait_for_status(page, "Added 2 photos.")

    status = convert(page)
    assert "1 photo could not be converted" in status
    assert page.locator(".badge.failed").count() == 1
    assert "not a readable HEIC" in page.text_content(".item:has(.badge.failed) .note")
    assert page.text_content("#convert") == "Retry 1 photo"
    # The converted photo is what the user wants next, so Download becomes the main button.
    assert "primary" in page.get_attribute("#download", "class")
    assert "primary" not in page.get_attribute("#convert", "class")


def test_folder_keeps_its_structure_and_dates(page, tmp_path, make_heic):
    root = tmp_path / "Camera Roll"
    a = make_heic(root / "a.heic")
    b = make_heic(root / "2024" / "b.HEIC")
    (root / "notes.txt").write_text("not a photo")
    os.utime(a, (1_700_000_000, 1_700_000_000))
    page.set_input_files("#folder-input", str(root))
    wait_for_status(page, "Added 2 photos.")
    convert(page)

    zip_path = download(page, tmp_path)
    assert page.locator(".item .name").all_text_contents() == ["a.heic", "b.HEIC"]  # sorted by folder, name
    with zipfile.ZipFile(zip_path) as archive:
        names = sorted(archive.namelist())
        assert names == ["2024/b.jpg", "a.jpg"]
        stamp = datetime(*archive.getinfo("a.jpg").date_time)
    assert abs((stamp - datetime.fromtimestamp(1_700_000_000)).total_seconds()) <= 2


def test_drag_and_drop(page, tmp_path, make_heic):
    src = make_heic(tmp_path / "dropped.heic")
    data = base64.b64encode(src.read_bytes()).decode()
    page.evaluate(
        """data => {
            const bytes = Uint8Array.from(atob(data), c => c.charCodeAt(0));
            const transfer = new DataTransfer();
            transfer.items.add(new File([bytes], "dropped.heic", { type: "image/heic" }));
            transfer.items.add(new File(["hello"], "notes.txt", { type: "text/plain" }));
            for (const type of ["dragenter", "dragover", "drop"]) {
                document.body.dispatchEvent(new DragEvent(type, { dataTransfer: transfer, bubbles: true, cancelable: true }));
            }
        }""",
        data,
    )
    status = wait_for_status(page, "Added 1 photo.")
    assert "Ignored 1 file that is not HEIC." in status
    assert page.text_content(".item .name") == "dropped.heic"


def test_settings_are_remembered(page, site_url):
    page.fill("#quality", "77")
    page.dispatch_event("#quality", "change")
    page.uncheck("#keep-metadata")
    page.reload()
    assert page.input_value("#quality") == "77"
    assert not page.is_checked("#keep-metadata")
    assert page.is_disabled("#remove-location")
