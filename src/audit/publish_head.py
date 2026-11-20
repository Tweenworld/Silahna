"""Publish the latest chain head outside the database (FR-A2)."""

from __future__ import annotations

from pathlib import Path


def publish_head(path: Path, mac: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(mac + "\n")


def read_published_head(path: Path) -> str:
    if not path.exists():
        return ""
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1]
