from __future__ import annotations

from pathlib import Path

import pytest
from PIL import ExifTags, Image, ImageCms

import heic2jpeg.converter  # noqa: F401 - registers the HEIF plugin with Pillow


def build_exif(*, orientation: int | None = None, with_gps: bool = True) -> Image.Exif:
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Apple"
    exif[ExifTags.Base.Model] = "iPhone 15"
    exif[ExifTags.Base.DateTime] = "2024:07:14 18:30:00"
    if orientation is not None:
        exif[ExifTags.Base.Orientation] = orientation
    if with_gps:
        exif.get_ifd(ExifTags.IFD.GPSInfo).update(
            {
                ExifTags.GPS.GPSLatitudeRef: "N",
                ExifTags.GPS.GPSLatitude: (47.0, 29.0, 0.0),
                ExifTags.GPS.GPSLongitudeRef: "E",
                ExifTags.GPS.GPSLongitude: (19.0, 2.0, 0.0),
            }
        )
    return exif


@pytest.fixture
def make_heic():
    """Create a real HEIC file. Returns the path."""

    def _make(
        path: Path,
        size: tuple[int, int] = (160, 120),
        *,
        mode: str = "RGB",
        color=(200, 60, 40),
        exif: Image.Exif | None = None,
        icc: bytes | None = None,
    ) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new(mode, size, color)
        kwargs = {"quality": 90}
        if exif is not None:
            kwargs["exif"] = exif.tobytes()
        if icc is not None:
            kwargs["icc_profile"] = icc
        image.save(path, format="HEIF", **kwargs)
        return path

    return _make


@pytest.fixture
def srgb_icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
