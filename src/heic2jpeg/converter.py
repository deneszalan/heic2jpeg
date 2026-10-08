"""HEIC -> JPEG conversion logic.

This module has no GUI dependencies so it can be tested (and reused) on its own.
"""

from __future__ import annotations

import errno
import io
import os
import sys
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

import pillow_heif
from PIL import ExifTags, Image, ImageOps, UnidentifiedImageError

pillow_heif.register_heif_opener()

# Phones like the Galaxy S2x Ultra can produce 100+ megapixel HEIF photos, which is
# above Pillow's default "decompression bomb" limit. These are the user's own files,
# so allow anything a real camera produces while still refusing absurd sizes.
Image.MAX_IMAGE_PIXELS = 400_000_000

HEIC_EXTENSIONS = frozenset({".heic", ".heif", ".hif"})

_MAX_RENAME_ATTEMPTS = 10_000


class ExistingFile(str, Enum):
    """What to do when the JPEG we want to write already exists."""

    RENAME = "rename"  # keep both: "IMG_0001 (1).jpg"
    OVERWRITE = "overwrite"
    SKIP = "skip"


class Outcome(str, Enum):
    CONVERTED = "converted"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ConversionOptions:
    quality: int = 92
    # None means "save next to the original photo".
    output_dir: Path | None = None
    keep_metadata: bool = True
    remove_location: bool = False
    # Longest edge in pixels; None keeps the original size.
    max_size: int | None = None
    existing: ExistingFile = ExistingFile.RENAME
    keep_file_dates: bool = True


@dataclass(frozen=True)
class SourceFile:
    path: Path
    # Sub-folder below a dropped folder. It is recreated inside the output folder so
    # that converting a folder tree keeps its structure.
    relative_dir: Path = Path()


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    output: Path
    outcome: Outcome
    output_size: int = 0


class ConversionError(Exception):
    """A conversion failed; the message is meant to be shown to the user."""


def is_heic(path: Path) -> bool:
    # "._IMG_0001.HEIC" files are macOS metadata leftovers on USB drives, not photos.
    return path.suffix.lower() in HEIC_EXTENSIONS and not path.name.startswith("._")


def path_key(path: Path | str) -> str:
    """A normalized key for detecting the same file added twice."""
    return os.path.normcase(os.path.abspath(path))


def collect_sources(paths: Iterable[str | os.PathLike]) -> list[SourceFile]:
    """Expand dropped files and folders into HEIC files. Folders are searched recursively."""
    found: list[SourceFile] = []
    seen: set[str] = set()

    def add(source: SourceFile) -> None:
        key = path_key(source.path)
        if key not in seen:
            seen.add(key)
            found.append(source)

    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for root, dirs, files in os.walk(path):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                root_path = Path(root)
                for name in sorted(files, key=str.lower):
                    file = root_path / name
                    if is_heic(file):
                        add(SourceFile(file, root_path.relative_to(path)))
        elif path.is_file() and is_heic(path):
            add(SourceFile(path))
    return found


def planned_output(source: SourceFile, options: ConversionOptions) -> Path:
    """Where the JPEG would be written before resolving name conflicts."""
    if options.output_dir is None:
        folder = source.path.parent
    else:
        folder = Path(options.output_dir) / source.relative_dir
    return folder / f"{source.path.stem}.jpg"


def convert_file(source: SourceFile | Path, options: ConversionOptions) -> ConversionResult:
    """Convert one HEIC file to JPEG.

    Raises ConversionError with a user-friendly message on failure.
    """
    if not isinstance(source, SourceFile):
        source = SourceFile(Path(source))
    planned = planned_output(source, options)

    if options.existing is ExistingFile.SKIP and planned.exists():
        return ConversionResult(source.path, planned, Outcome.SKIPPED)

    data = encode_jpeg(source.path, options)

    try:
        planned.parent.mkdir(parents=True, exist_ok=True)
        target, placeholder = _claim_target(planned, options.existing)
        if target is None:
            return ConversionResult(source.path, planned, Outcome.SKIPPED)
        try:
            _write_atomically(target, data)
        except BaseException:
            if placeholder:
                _remove_quietly(target)
            raise
    except OSError as exc:
        raise ConversionError(_describe_os_error(exc, planned.parent)) from exc

    if options.keep_file_dates:
        copy_file_dates(source.path, target)
    return ConversionResult(source.path, target, Outcome.CONVERTED, len(data))


def encode_jpeg(path: Path, options: ConversionOptions) -> bytes:
    """Decode a HEIC file and return the JPEG-encoded bytes."""
    try:
        image = Image.open(path)
    except FileNotFoundError as exc:
        raise ConversionError("The file no longer exists.") from exc
    except UnidentifiedImageError as exc:
        raise ConversionError("This is not a readable HEIC photo (the file may be damaged).") from exc
    except OSError as exc:
        raise ConversionError(_describe_os_error(exc, path.parent)) from exc

    with image:
        try:
            image.load()
        except Exception as exc:  # noqa: BLE001 - decoder errors come in many types
            raise ConversionError(f"The photo could not be decoded: {exc}") from exc

        icc_profile = image.info.get("icc_profile")
        xmp = image.info.get("xmp") if options.keep_metadata and not options.remove_location else None

        # libheif already applies HEIF rotation and resets the EXIF orientation, so this
        # is a no-op for real HEIC files. It matters for files that are secretly JPEGs.
        result = ImageOps.exif_transpose(image)

    exif = result.getexif() if options.keep_metadata else None
    result = _flatten_to_rgb(result)

    if options.max_size and max(result.size) > options.max_size:
        result.thumbnail((options.max_size, options.max_size), Image.Resampling.LANCZOS)

    exif_bytes = _prepare_exif(exif, result.size, options) if exif is not None else None

    save_args: dict = {"format": "JPEG", "quality": options.quality, "optimize": True}
    if icc_profile:
        # Always keep the colour profile: iPhone photos use Display P3 and look
        # washed out without it.
        save_args["icc_profile"] = icc_profile
    if exif_bytes:
        save_args["exif"] = exif_bytes
    if xmp:
        save_args["xmp"] = xmp

    buffer = io.BytesIO()
    try:
        result.save(buffer, **save_args)
    except ValueError:
        # JPEG limits metadata blocks to 64 KB. Rather than fail, save without it.
        save_args.pop("exif", None)
        save_args.pop("xmp", None)
        buffer = io.BytesIO()
        result.save(buffer, **save_args)
    return buffer.getvalue()


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    """JPEG has no transparency: put transparent images on a white background."""
    if image.mode in ("RGB", "L"):
        return image
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def _prepare_exif(exif: Image.Exif, size: tuple[int, int], options: ConversionOptions) -> bytes | None:
    if not exif:
        return None
    if options.remove_location:
        exif.get_ifd(ExifTags.IFD.GPSInfo).clear()
        exif.pop(ExifTags.IFD.GPSInfo, None)
    exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    if ExifTags.Base.ExifImageWidth in exif_ifd or ExifTags.Base.ExifImageHeight in exif_ifd:
        exif_ifd[ExifTags.Base.ExifImageWidth], exif_ifd[ExifTags.Base.ExifImageHeight] = size
    exif[ExifTags.Base.Orientation] = 1
    return exif.tobytes()


def _numbered_names(planned: Path):
    yield planned
    for n in range(1, _MAX_RENAME_ATTEMPTS):
        yield planned.with_name(f"{planned.stem} ({n}){planned.suffix}")


def _claim_target(planned: Path, policy: ExistingFile) -> tuple[Path | None, bool]:
    """Pick the output path and reserve it.

    Returns (path, created_placeholder). For "keep both" and "skip" the name is
    reserved by creating an empty file exclusively, so two photos converted at the
    same time can never pick the same name.
    """
    if policy is ExistingFile.OVERWRITE:
        return planned, False
    candidates = [planned] if policy is ExistingFile.SKIP else _numbered_names(planned)
    for candidate in candidates:
        try:
            with open(candidate, "xb"):
                pass
        except FileExistsError:
            continue
        return candidate, True
    if policy is ExistingFile.SKIP:
        return None, False
    raise ConversionError(f"Could not find a free file name for “{planned.name}”.")


def _write_atomically(target: Path, data: bytes) -> None:
    """Write via a temporary file so a crash never leaves a half-written JPEG behind."""
    temp = target.with_name(f".{target.stem}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(temp, "xb") as f:
            f.write(data)
        os.replace(temp, target)
    except BaseException:
        _remove_quietly(temp)
        raise


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _describe_os_error(exc: OSError, folder: Path) -> str:
    if isinstance(exc, PermissionError):
        return f"Windows did not allow saving in “{folder}”. The file may be open in another program, or the folder is protected."
    if exc.errno == errno.ENOSPC:
        return "There is not enough free disk space."
    if isinstance(exc, FileNotFoundError):
        return "The file or folder no longer exists."
    return f"Could not save the file: {exc.strerror or exc}"


def copy_file_dates(source: Path, target: Path) -> None:
    """Give the JPEG the same 'modified' (and on Windows 'created') date as the original."""
    try:
        st = source.stat()
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
        if sys.platform == "win32":
            created_ns = getattr(st, "st_birthtime_ns", None) or st.st_ctime_ns
            _set_windows_creation_time(target, created_ns)
    except OSError:
        pass  # Dates are a nice-to-have; never fail a conversion over them.


def _set_windows_creation_time(path: Path, created_ns: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.SetFileTime.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.c_void_p, ctypes.c_void_p,
    ]
    kernel32.SetFileTime.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    file_write_attributes = 0x100
    share_all = 0x1 | 0x2 | 0x4
    open_existing = 3
    invalid_handle = wintypes.HANDLE(-1).value

    handle = kernel32.CreateFileW(str(path), file_write_attributes, share_all, None, open_existing, 0, None)
    if handle is None or handle == invalid_handle:
        return
    try:
        # FILETIME counts 100 ns intervals since 1601-01-01.
        ticks = created_ns // 100 + 116_444_736_000_000_000
        filetime = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
        kernel32.SetFileTime(handle, ctypes.byref(filetime), None, None)
    finally:
        kernel32.CloseHandle(handle)
