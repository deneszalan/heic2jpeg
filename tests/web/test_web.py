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
    # WEB_BROWSER=webkit runs the same tests in Safari's engine.
    engine = os.environ.get("WEB_BROWSER", "chromium")
    with playwright_api.sync_playwright() as p:
        if engine == "chromium":
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        else:
            browser = getattr(p, engine).launch()
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


# --- iPhone: Safari may hand over photos already converted to JPEG -----------------------


def make_jpeg(path: Path, size=(400, 200), orientation: int | None = None) -> Path:
    image = Image.effect_noise(size, 40).convert("RGB")
    image.save(path, format="JPEG", quality=90, exif=build_exif(orientation=orientation).tobytes())
    return path


def image_data(jpeg: bytes) -> bytes:
    """The compressed image data (from Start of Scan on) of a JPEG."""
    return jpeg[jpeg.index(b"\xff\xda"):]


def test_picked_jpeg_is_kept_without_quality_loss(page, tmp_path):
    src = make_jpeg(tmp_path / "IMG_0007.jpg", orientation=6)
    page.check("#remove-location")
    page.set_input_files("#file-input", str(src))
    wait_for_status(page, "Added 1 photo.")
    convert(page)

    out = download(page, tmp_path / "out")
    assert out.name == "IMG_0007.jpg"
    assert image_data(out.read_bytes()) == image_data(src.read_bytes())  # pixels untouched
    with Image.open(out) as jpeg:
        exif = jpeg.getexif()
        assert exif[ExifTags.Base.Make] == "Apple"
        assert exif[ExifTags.Base.Orientation] == 6  # pixels weren't rotated, so the tag stays
        assert ExifTags.IFD.GPSInfo not in exif


def test_picked_jpeg_is_resized_upright(page, tmp_path):
    src = make_jpeg(tmp_path / "wide.jpg", size=(2000, 1000), orientation=6)
    page.select_option("#size", "1280")
    page.set_input_files("#file-input", str(src))
    wait_for_status(page, "Added 1 photo.")
    convert(page)

    with Image.open(download(page, tmp_path / "out")) as jpeg:
        assert jpeg.size == (640, 1280)  # rotated upright, then shrunk
        assert jpeg.getexif()[ExifTags.Base.Orientation] == 1


def test_jpegs_inside_folders_are_left_alone(page, tmp_path, make_heic):
    root = tmp_path / "Mixed"
    make_heic(root / "a.heic")
    make_jpeg(root / "already.jpg")
    page.set_input_files("#folder-input", str(root))
    wait_for_status(page, "Added 1 photo.")
    assert page.locator(".item .name").all_text_contents() == ["a.heic"]


IPHONE = {
    "viewport": {"width": 393, "height": 852},
    "user_agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "device_scale_factor": 3,
    "has_touch": True,
}


@pytest.fixture
def iphone(browser, site_url):
    options = dict(IPHONE)
    if browser.browser_type.name != "firefox":
        options["is_mobile"] = True
    context = browser.new_context(accept_downloads=True, **options)
    # Stand-in for the iOS share sheet: records what would be shared.
    context.add_init_script(
        """
        navigator.canShare = () => true;
        navigator.share = async (data) => {
            window.shared = data.files.map(f => ({ name: f.name, type: f.type, size: f.size }));
        };
        """
    )
    page = context.new_page()
    page.goto(site_url)
    yield page
    context.close()


def test_iphone_layout_and_save_to_photos(iphone, tmp_path, make_heic):
    assert iphone.is_hidden("#choose-folder")  # Safari on iPhone can't pick folders
    assert iphone.is_visible("text=Convert your HEIC photos to JPEG")
    assert iphone.evaluate("window.heic2jpeg.limits") == {"maxPixels": 4096 * 4096, "workers": 1}

    photos = [make_heic(tmp_path / f"IMG_{i}.HEIC") for i in (1, 2)]
    iphone.set_input_files("#file-input", [str(p) for p in photos])
    wait_for_status(iphone, "Added 2 photos.")
    status = convert(iphone)
    assert "Tap Save to Photos, then choose “Save 2 Images”." in status

    assert iphone.text_content("#share") == "Save to Photos…"
    assert "primary" in iphone.get_attribute("#share", "class")
    iphone.click("#share")
    shared = iphone.wait_for_function("() => window.shared").json_value()
    assert [(f["name"], f["type"]) for f in shared] == [("IMG_1.jpg", "image/jpeg"), ("IMG_2.jpg", "image/jpeg")]
    assert all(f["size"] > 0 for f in shared)


def test_photos_too_big_for_the_browser_are_shrunk(iphone, tmp_path, make_heic):
    src = make_heic(tmp_path / "huge.heic", size=(320, 240))
    iphone.evaluate("window.heic2jpeg.limits.maxPixels = 20000")  # pretend the limit is tiny
    iphone.set_input_files("#file-input", str(src))
    wait_for_status(iphone, "Added 1 photo.")
    convert(iphone)

    assert "too big for this browser" in iphone.text_content(".item .note")
    with iphone.expect_download() as info:
        iphone.click("#download")
    with Image.open(info.value.path()) as jpeg:
        width, height = jpeg.size
        assert width * height <= 20000
        assert abs(width / height - 320 / 240) < 0.02
