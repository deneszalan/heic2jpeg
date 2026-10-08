from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import ExifTags, Image

from heic2jpeg.converter import (
    ConversionError,
    ConversionOptions,
    ExistingFile,
    Outcome,
    SourceFile,
    collect_sources,
    convert_file,
    planned_output,
)

from .conftest import build_exif


def open_jpeg(path: Path) -> Image.Image:
    image = Image.open(path)
    assert image.format == "JPEG"
    return image


def leftovers(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*") if p.name.endswith(".tmp")]


def test_converts_next_to_original(tmp_path, make_heic):
    src = make_heic(tmp_path / "IMG_0001.HEIC", size=(160, 120))

    result = convert_file(src, ConversionOptions())

    assert result.outcome is Outcome.CONVERTED
    assert result.output == tmp_path / "IMG_0001.jpg"
    assert result.output_size == result.output.stat().st_size
    with open_jpeg(result.output) as jpg:
        assert jpg.size == (160, 120)
        assert jpg.mode == "RGB"
        r, g, b = jpg.getpixel((80, 60))
        assert abs(r - 200) < 12 and abs(g - 60) < 12 and abs(b - 40) < 12
    assert leftovers(tmp_path) == []


def test_output_folder_mirrors_subfolders(tmp_path, make_heic):
    photos = tmp_path / "photos"
    make_heic(photos / "a.heic")
    make_heic(photos / "trip" / "day1" / "b.heif")
    out = tmp_path / "out"
    options = ConversionOptions(output_dir=out)

    for source in collect_sources([photos]):
        convert_file(source, options)

    assert (out / "a.jpg").is_file()
    assert (out / "trip" / "day1" / "b.jpg").is_file()


def test_lower_quality_gives_smaller_file(tmp_path, make_heic):
    src = tmp_path / "noise.heic"
    Image.effect_noise((256, 256), 60).convert("RGB").save(src, format="HEIF", quality=95)

    high = convert_file(src, ConversionOptions(quality=95, output_dir=tmp_path / "high"))
    low = convert_file(src, ConversionOptions(quality=50, output_dir=tmp_path / "low"))

    assert low.output_size < high.output_size


def test_metadata_is_kept_by_default(tmp_path, make_heic):
    src = make_heic(tmp_path / "meta.heic", exif=build_exif())

    result = convert_file(src, ConversionOptions())

    with open_jpeg(result.output) as jpg:
        exif = jpg.getexif()
        assert exif[ExifTags.Base.Make] == "Apple"
        assert exif[ExifTags.Base.DateTime] == "2024:07:14 18:30:00"
        assert exif.get_ifd(ExifTags.IFD.GPSInfo)[ExifTags.GPS.GPSLatitudeRef] == "N"


def test_remove_location_keeps_other_metadata(tmp_path, make_heic):
    src = make_heic(tmp_path / "meta.heic", exif=build_exif())

    result = convert_file(src, ConversionOptions(remove_location=True))

    with open_jpeg(result.output) as jpg:
        exif = jpg.getexif()
        assert exif[ExifTags.Base.Make] == "Apple"
        assert ExifTags.IFD.GPSInfo not in exif
        assert not exif.get_ifd(ExifTags.IFD.GPSInfo)


def test_metadata_can_be_dropped(tmp_path, make_heic):
    src = make_heic(tmp_path / "meta.heic", exif=build_exif())

    result = convert_file(src, ConversionOptions(keep_metadata=False))

    with open_jpeg(result.output) as jpg:
        assert not jpg.getexif()


def test_color_profile_is_always_kept(tmp_path, make_heic, srgb_icc):
    src = make_heic(tmp_path / "icc.heic", icc=srgb_icc)

    result = convert_file(src, ConversionOptions(keep_metadata=False))

    with open_jpeg(result.output) as jpg:
        assert jpg.info.get("icc_profile") == srgb_icc


def test_rotation_is_applied(tmp_path, make_heic):
    # Orientation 6 = "rotate 90° clockwise to display correctly".
    src = make_heic(tmp_path / "portrait.heic", size=(160, 96), exif=build_exif(orientation=6))

    result = convert_file(src, ConversionOptions())

    with open_jpeg(result.output) as jpg:
        assert jpg.size == (96, 160)
        assert jpg.getexif().get(ExifTags.Base.Orientation, 1) == 1


def test_resize_limits_longest_edge(tmp_path, make_heic):
    src = make_heic(tmp_path / "big.heic", size=(400, 200), exif=build_exif())

    result = convert_file(src, ConversionOptions(max_size=100))

    with open_jpeg(result.output) as jpg:
        assert jpg.size == (100, 50)


def test_resize_never_upscales(tmp_path, make_heic):
    src = make_heic(tmp_path / "small.heic", size=(160, 120))

    result = convert_file(src, ConversionOptions(max_size=2048))

    with open_jpeg(result.output) as jpg:
        assert jpg.size == (160, 120)


def test_transparency_becomes_white(tmp_path, make_heic):
    src = make_heic(tmp_path / "alpha.heic", mode="RGBA", color=(0, 0, 0, 0))

    result = convert_file(src, ConversionOptions())

    with open_jpeg(result.output) as jpg:
        assert jpg.mode == "RGB"
        assert all(channel > 240 for channel in jpg.getpixel((10, 10)))


def test_existing_file_keep_both(tmp_path, make_heic):
    src = make_heic(tmp_path / "IMG.heic")
    (tmp_path / "IMG.jpg").write_bytes(b"existing")

    first = convert_file(src, ConversionOptions(existing=ExistingFile.RENAME))
    second = convert_file(src, ConversionOptions(existing=ExistingFile.RENAME))

    assert first.output.name == "IMG (1).jpg"
    assert second.output.name == "IMG (2).jpg"
    assert (tmp_path / "IMG.jpg").read_bytes() == b"existing"


def test_existing_file_overwrite(tmp_path, make_heic):
    src = make_heic(tmp_path / "IMG.heic")
    (tmp_path / "IMG.jpg").write_bytes(b"existing")

    result = convert_file(src, ConversionOptions(existing=ExistingFile.OVERWRITE))

    assert result.outcome is Outcome.CONVERTED
    assert result.output == tmp_path / "IMG.jpg"
    open_jpeg(result.output).close()


def test_existing_file_skip(tmp_path, make_heic):
    src = make_heic(tmp_path / "IMG.heic")
    (tmp_path / "IMG.jpg").write_bytes(b"existing")

    result = convert_file(src, ConversionOptions(existing=ExistingFile.SKIP))

    assert result.outcome is Outcome.SKIPPED
    assert (tmp_path / "IMG.jpg").read_bytes() == b"existing"


def test_parallel_conversions_never_share_a_name(tmp_path, make_heic):
    sources = [make_heic(tmp_path / f"folder{i}" / "IMG_0001.heic") for i in range(8)]
    options = ConversionOptions(output_dir=tmp_path / "out")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda s: convert_file(s, options), sources))

    outputs = {r.output for r in results}
    assert len(outputs) == 8
    for output in outputs:
        open_jpeg(output).close()


def test_file_dates_are_copied(tmp_path, make_heic):
    src = make_heic(tmp_path / "dated.heic")
    os.utime(src, (1_600_000_000, 1_600_000_000))

    result = convert_file(src, ConversionOptions())

    assert result.output.stat().st_mtime == pytest.approx(1_600_000_000, abs=1)


@pytest.mark.skipif(sys.platform != "win32", reason="creation date is Windows-specific")
def test_creation_date_is_copied_on_windows(tmp_path, make_heic):
    src = make_heic(tmp_path / "dated.heic")

    result = convert_file(src, ConversionOptions())

    assert result.output.stat().st_birthtime == pytest.approx(src.stat().st_birthtime, abs=0.01)


def test_file_dates_can_be_left_alone(tmp_path, make_heic):
    src = make_heic(tmp_path / "dated.heic")
    os.utime(src, (1_600_000_000, 1_600_000_000))

    result = convert_file(src, ConversionOptions(keep_file_dates=False))

    assert result.output.stat().st_mtime > 1_600_000_000 + 86_400


def test_damaged_file_reports_friendly_error(tmp_path):
    src = tmp_path / "broken.heic"
    src.write_bytes(b"this is not an image")

    with pytest.raises(ConversionError, match="not a readable HEIC"):
        convert_file(src, ConversionOptions())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["broken.heic"]


def test_truncated_file_leaves_nothing_behind(tmp_path, make_heic):
    good = make_heic(tmp_path / "good.heic", size=(320, 240))
    src = tmp_path / "truncated.heic"
    src.write_bytes(good.read_bytes()[: good.stat().st_size // 2])

    with pytest.raises(ConversionError):
        convert_file(src, ConversionOptions())
    assert not (tmp_path / "truncated.jpg").exists()
    assert leftovers(tmp_path) == []


def test_missing_file(tmp_path):
    with pytest.raises(ConversionError, match="no longer exists"):
        convert_file(tmp_path / "gone.heic", ConversionOptions())


def test_collect_sources(tmp_path, make_heic):
    root = tmp_path / "root"
    a = make_heic(root / "A.HEIC")
    b = make_heic(root / "sub" / "b.heif")
    c = make_heic(root / "sub" / "deeper" / "c.hif")
    (root / "notes.txt").write_text("hello")
    (root / "photo.jpg").write_bytes(b"jpg")
    (root / "._A.HEIC").write_bytes(b"macOS junk")
    make_heic(root / ".hidden" / "secret.heic")
    single = make_heic(tmp_path / "single.heic")

    sources = collect_sources([root, single, a, tmp_path / "missing.heic"])

    assert [s.path for s in sources] == [a, b, c, single]
    assert [s.relative_dir for s in sources] == [Path(), Path("sub"), Path("sub/deeper"), Path()]


def test_planned_output():
    source = SourceFile(Path("/photos/trip/IMG_1.HEIC"), Path("trip"))
    assert planned_output(source, ConversionOptions()) == Path("/photos/trip/IMG_1.jpg")
    assert planned_output(source, ConversionOptions(output_dir=Path("/out"))) == Path("/out/trip/IMG_1.jpg")
