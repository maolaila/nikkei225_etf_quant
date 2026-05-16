from __future__ import annotations

from pathlib import Path


def root_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def ensure_dir(path: str | Path) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = root_dir() / resolved
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def resolve_path(path: str | Path) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = root_dir() / resolved
    return resolved

