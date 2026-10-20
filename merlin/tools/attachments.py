"""Resolve local files and the latest MERLIN screenshot for app integrations."""
from __future__ import annotations

from pathlib import Path

SCREENSHOTS_DIR = Path.home() / "merlin" / "screenshots"
_LATEST_ALIASES = frozenset({"last", "latest", "screenshot", "captura",
                            "última", "ultima"})


def latest_screenshot() -> Path | None:
    """Return the newest MERLIN screenshot, if the screenshot folder exists."""
    if not SCREENSHOTS_DIR.is_dir():
        return None
    files = sorted(SCREENSHOTS_DIR.glob("merlin_*.png"),
                   key=lambda item: (item.stat().st_mtime_ns, item.name))
    return files[-1] if files else None


def resolve_file(value: str | Path | None) -> Path | None:
    """Resolve a file path or a latest-screenshot alias to an existing file."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.casefold() in _LATEST_ALIASES:
        return latest_screenshot()
    path = Path(raw).expanduser()
    try:
        return path.resolve(strict=True) if path.is_file() else None
    except (OSError, RuntimeError):
        return None
