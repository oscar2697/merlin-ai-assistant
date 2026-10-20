"""Stage image and text posts for Facebook and WhatsApp Web.

The composer remains open for review. Publishing is a separate operation that
requires an explicit confirmation and a matching in-memory staging token.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from merlin.tools.attachments import resolve_file

logger = logging.getLogger("merlin.tools.social")
_PROFILE_ROOT = Path.home() / "merlin" / "browser_profiles"
_EXECUTOR: ThreadPoolExecutor | None = None
_BROWSER: dict[str, dict] = {}
_STAGED: dict[str, dict] = {}

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_FILE_INPUTS = ('input[type="file"][accept*="image"]',
                'input[type="file"][accept*="image/*"]',
                'input[type="file"]')
_POST_TEXT = {
    "facebook": ("What's on your mind?", "¿Qué estás pensando?"),
}
_STORY_BUTTONS = {
    "facebook": ("Create story", "Crear historia"),
    "whatsapp": ("Status", "Updates", "Estados", "Novedades"),
}
_PUBLISH_BUTTONS = {
    ("facebook", "post"): ("Post", "Publicar"),
    ("facebook", "story"): ("Share to story", "Compartir en historia",
                               "Share story", "Compartir historia"),
    ("whatsapp", "story"): ("Send", "Enviar", "Share", "Compartir"),
}


def _on_browser_thread(fn, timeout_s: float = 180):
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(max_workers=1,
                                       thread_name_prefix="merlin-social-browser")
    return _EXECUTOR.submit(fn).result(timeout_s)


def _page(platform: str):
    from playwright.sync_api import sync_playwright

    state = _BROWSER.get(platform)
    if state is None:
        profile = _PROFILE_ROOT / platform
        profile.mkdir(parents=True, exist_ok=True)
        pw = sync_playwright().start()
        context = pw.chromium.launch_persistent_context(
            str(profile), headless=False, viewport={"width": 1280, "height": 860})
        page = context.pages[0] if context.pages else context.new_page()
        state = {"pw": pw, "context": context, "page": page}
        _BROWSER[platform] = state
    return state["page"]


def _click_text(scope, labels: tuple[str, ...], timeout_ms: int = 3500) -> bool:
    for label in labels:
        trimmed = label.strip()
        pattern = re.compile(rf"^{re.escape(trimmed)}$", re.I)
        for role in ("button", "link"):
            try:
                scope.get_by_role(role, name=pattern).first.click(
                    timeout=timeout_ms)
                return True
            except Exception:
                continue
        try:
            scope.get_by_text(pattern, exact=True).first.click(
                timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


def _dialog(page):
    dialogs = page.get_by_role("dialog")
    return dialogs.last if dialogs.count() else page


def _attach_image(scope, path: str) -> bool:
    for selector in _FILE_INPUTS:
        try:
            locator = scope.locator(selector).first
            locator.set_input_files(path, timeout=5000)
            name = Path(path).name.casefold()
            attached = locator.evaluate(
                "(element, expected) => Array.from(element.files || []).some(file => "
                "file.name.toLowerCase() === expected)", name)
            if attached:
                return True
        except Exception:
            continue
    return False


def _file_is_still_attached(scope, filename: str) -> bool:
    if not filename:
        return True
    for selector in _FILE_INPUTS:
        try:
            locators = scope.locator(selector)
            for index in range(locators.count()):
                if locators.nth(index).evaluate(
                        "(element, name) => Array.from(element.files || []).some(" 
                        "file => file.name.toLowerCase() === name)",
                        filename.casefold()):
                    return True
        except Exception:
            continue
    return False


def _logged_out(page, platform: str) -> bool:
    url = page.url.casefold()
    markers = ("login", "signin", "checkpoint") if platform == "facebook" else (
        "web.whatsapp.com", "login")
    if platform == "facebook":
        return any(marker in url for marker in markers)
    return "web.whatsapp.com" not in url or "login" in url


def _open_composer(page, platform: str, mode: str) -> bool:
    if platform == "facebook" and mode == "post":
        for label in _POST_TEXT[platform]:
            trimmed = label.strip()
            pattern = re.compile(rf"^{re.escape(trimmed)}$", re.I)
            try:
                page.get_by_text(pattern, exact=True).first.click(
                    timeout=4000)
                return True
            except Exception:
                continue
        return False
    if platform == "facebook" and mode == "story":
        return _click_text(page, _STORY_BUTTONS[platform])
    if platform == "whatsapp" and mode == "story":
        if not _click_text(page, _STORY_BUTTONS[platform]):
            return False
        page.wait_for_timeout(1000)
        return _click_text(page, ("Add status", "Añadir estado", "My status",
                                  "Mi estado", "Add to my status", "Añadir a mi estado"))
    return False


def _stage(platform: str, mode: str, text: str, file: str | None,
           stage_id: str) -> dict:
    page = _page(platform)
    url = "https://www.facebook.com/" if platform == "facebook" else \
        "https://web.whatsapp.com/"
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    if _logged_out(page, platform):
        return {"ok": False, "need_login": True,
                "error": f"Sign in to {platform.title()} in the opened browser first."}
    if not _open_composer(page, platform, mode):
        return {"ok": False, "error": f"Could not open the {platform} {mode} composer."}
    page.wait_for_timeout(1000)
    if not page.get_by_role("dialog").count():
        return {"ok": False,
                "error": f"Could not identify a safe {platform} composer dialog; no content was entered."}
    composer = _dialog(page)

    if file and not _attach_image(composer, file):
        return {"ok": False, "error": f"Could not attach the image to the {platform} composer."}
    entered_text = ""
    if text:
        editors = composer.locator('[contenteditable="true"], textarea, [role="textbox"]')
        try:
            editor = editors.first
            editor.wait_for(timeout=5000)
            editor.fill(text, timeout=5000)
            entered_text = text
        except Exception:
            if platform == "facebook" and mode == "story" and file:
                # Facebook's story composer supports image-only stories.
                pass
            else:
                return {"ok": False, "error": f"Could not enter text in the {platform} composer."}

    _STAGED[platform] = {"id": stage_id, "mode": mode, "page": page,
                         "text": entered_text,
                         "file_name": Path(file).name if file else ""}
    return {"ok": True, "staged": True, "needs_confirmation": True,
            "stage_id": stage_id, "platform": platform, "mode": mode,
            "text": text, "file": file, "method": "playwright"}


def _publish(platform: str, mode: str, stage_id: str) -> dict:
    staged = _STAGED.get(platform)
    if not staged or staged.get("id") != stage_id or staged.get("mode") != mode:
        return {"ok": False, "error": "The staged post expired or no longer matches this confirmation."}
    page = staged["page"]
    if not page.get_by_role("dialog").count():
        _STAGED.pop(platform, None)
        return {"ok": False, "error": "The reviewed composer is no longer open; nothing was published."}
    composer = _dialog(page)
    if not _file_is_still_attached(composer, staged.get("file_name", "")):
        return {"ok": False,
                "error": "The staged image is no longer attached; nothing was published."}
    expected_text = staged.get("text", "")
    if expected_text:
        try:
            content = composer.locator(
                '[contenteditable="true"], textarea, [role="textbox"]').first
            try:
                current_text = content.input_value(timeout=1500)
            except Exception:
                current_text = content.inner_text(timeout=1500)
            if " ".join(current_text.split()) != " ".join(expected_text.split()):
                return {"ok": False,
                        "error": "The staged text changed after review; nothing was published."}
        except Exception:
            return {"ok": False,
                    "error": "Could not verify the reviewed text; nothing was published."}
    labels = _PUBLISH_BUTTONS.get((platform, mode), ())
    if not _click_text(composer, labels, timeout_ms=5000):
        return {"ok": False, "error": f"Could not find the {platform} {mode} publish button in the open composer."}
    page.wait_for_timeout(2500)
    if page.get_by_role("dialog").count():
        return {"ok": False, "error": f"The composer dialog did not close after clicking publish; publication may have failed."}
    _STAGED.pop(platform, None)
    return {"ok": True, "published": True, "platform": platform,
            "mode": mode, "method": "playwright"}


def publish_social(platform: str, mode: str, text: str = "",
                   file_path: str = "", publish: bool = False,
                   stage_id: str = "") -> dict:
    """Stage or publish a Facebook post/story or WhatsApp image status."""
    platform = (platform or "").strip().lower()
    mode = (mode or "").strip().lower()
    if platform not in ("facebook", "whatsapp"):
        return {"ok": False, "error": "Supported platforms are Facebook and WhatsApp Web."}
    if mode not in (("post", "story") if platform == "facebook" else ("story",)):
        return {"ok": False, "error": f"Unsupported {platform} publication type: {mode}."}
    if publish:
        if not stage_id:
            return {"ok": False, "error": "A matching staged post is required before publishing."}
        return _on_browser_thread(lambda: _publish(platform, mode, stage_id))

    requested = file_path.strip()
    file = resolve_file(requested) if requested else None
    if requested and file is None:
        return {"ok": False, "error": "The attachment file does not exist."}
    if mode == "story" and file is None:
        return {"ok": False, "error": "Stories/status updates require an image attachment."}
    if file and (file.suffix.casefold() not in _IMAGE_SUFFIXES or
                 file.stat().st_size == 0):
        return {"ok": False, "error": "Stories and these social composers accept non-empty image files only."}
    token = uuid.uuid4().hex
    try:
        return _on_browser_thread(lambda: _stage(platform, mode, text,
                                                 str(file) if file else None, token))
    except Exception as exc:
        logger.exception("Social publication staging failed")
        return {"ok": False, "error": str(exc)}
