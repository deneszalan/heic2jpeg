# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the portable, single-file Windows program.

Build from the repository root:

    pyinstaller --noconfirm packaging/heic2jpeg.spec

The result is dist/HEIC-to-JPEG-Converter.exe, which runs without installation.
"""

import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
PACKAGE = SRC / "heic2jpeg"
VERSION = re.search(r'__version__ = "([^"]+)"', (PACKAGE / "__init__.py").read_text()).group(1)
EXE_NAME = "HEIC-to-JPEG-Converter"

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(SRC)],
    datas=[(str(PACKAGE / "resources"), "heic2jpeg/resources")],
    excludes=[
        "tkinter",
        "unittest",
        "pydoc",
        "pytest",
        "PySide6.QtNetwork",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtOpenGL",
        "PySide6.QtPdf",
        "PySide6.QtSvg",
    ],
    noarchive=False,
)

# Qt brings along pieces this app never uses. Leaving them out makes the .exe smaller,
# which also makes it start faster (a single-file program unpacks itself on launch).
UNUSED = (
    "opengl32sw.dll",  # software OpenGL renderer (~20 MB)
    "/translations/",
    "qt6quick",
    "qt6qml",
    "qt6pdf",
    "qt6virtualkeyboard",
    "qt6network",
    "/qml/",
    "/tls/",
    "/networkinformation/",
    "/platforminputcontexts/",
    "imageformats/qpdf",
)


def _needed(entry) -> bool:
    name = "/" + entry[0].replace("\\", "/").lower()
    return not any(pattern in name for pattern in UNUSED)


a.binaries = [entry for entry in a.binaries if _needed(entry)]
a.datas = [entry for entry in a.datas if _needed(entry)]

version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = tuple(int(part) for part in (VERSION.split(".") + ["0"] * 4)[:4])
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("FileDescription", "HEIC to JPEG Converter"),
                            StringStruct("ProductName", "HEIC to JPEG Converter"),
                            StringStruct("FileVersion", VERSION),
                            StringStruct("ProductVersion", VERSION),
                            StringStruct("OriginalFilename", f"{EXE_NAME}.exe"),
                            StringStruct("InternalName", EXE_NAME),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=EXE_NAME,
    icon=str(PACKAGE / "resources" / "app.ico"),
    version=version_info,
    console=False,
    disable_windowed_traceback=False,
    upx=False,
    strip=False,
    runtime_tmpdir=None,
)
