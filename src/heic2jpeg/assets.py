"""Locate bundled resource files, both from source and inside the packaged .exe."""

from pathlib import Path

RESOURCE_DIR = Path(__file__).resolve().parent / "resources"


def asset_path(name: str) -> str:
    return str(RESOURCE_DIR / name)
