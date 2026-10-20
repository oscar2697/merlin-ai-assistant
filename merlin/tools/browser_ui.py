"""Best-effort bridge between Playwright tools and MERLIN's visible cursor.

Browser automation runs in a separate window, so without this the floating
cursor/avatar stays idle while MERLIN stages a post. Everything here is optional:
CLI and headless runs simply skip the overlay work.
"""
from __future__ import annotations

import contextlib
import logging

logger = logging.getLogger("merlin.tools.browser_ui")


def _overlay():
    try:
        from merlin.gui import cursor_overlay
        return cursor_overlay
    except Exception:
        return None


def agent_mode():
    """Keep MERLIN's cursor visible for the whole browser operation."""
    overlay = _overlay()
    if overlay is None:
        return contextlib.nullcontext()
    try:
        return overlay.agent_mode()
    except Exception:
        return contextlib.nullcontext()


def _element_point(page, selector) -> tuple[float, float] | None:
    if not selector:
        return None
    selectors = (selector,) if isinstance(selector, str) else tuple(selector)
    box = None
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count():
                box = loc.bounding_box()
                if box:
                    break
        except Exception:
            continue
    if not box:
        return None
    try:
        view = page.evaluate(
            "() => ({x: window.screenX || 0, y: window.screenY || 0,"
            " dpr: window.devicePixelRatio || 1,"
            " chrome: Math.max(0, (window.outerHeight || 0) -"
            " (window.innerHeight || 0))})")
        dpr = float(view.get("dpr") or 1)
        return ((view.get("x", 0) + box["x"] + box["width"] / 2) * dpr,
                (view.get("y", 0) + view.get("chrome", 0) +
                 box["y"] + box["height"] / 2) * dpr)
    except Exception:
        return None


def announce(page, message: str, selector=None) -> None:
    """Show the working chip plus a bubble, and point at the target element."""
    overlay = _overlay()
    if overlay is None:
        return
    try:
        overlay.set_state("working", message)
    except Exception:
        logger.debug("could not publish the working state", exc_info=True)
    try:
        overlay.task_step(message, target=_element_point(page, selector))
    except Exception:
        logger.debug("could not announce the browser step", exc_info=True)
