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
from merlin.tools import browser_ui

logger = logging.getLogger("merlin.tools.social")
_PROFILE_ROOT = Path.home() / "merlin" / "browser_profiles"
_SCREENSHOTS = Path.home() / "merlin" / "screenshots"
_EXECUTOR: ThreadPoolExecutor | None = None
_BROWSER: dict[str, dict] = {}
_STAGED: dict[str, dict] = {}
_UPLOAD_HINT: str | None = None   # path handed to a native file chooser
_UPLOAD_DONE = False              # the site's own picker accepted that path

_WHATSAPP_QR_SELECTORS = (
    'canvas[aria-label*="Scan" i]',
    'canvas[aria-label*="Escanea" i]',
    'canvas[aria-label*="Código QR" i]',
    'div[data-ref]',
    'text="Link with phone number"',
    'text="Vincular con el número de teléfono"',
)
_WHATSAPP_READY_SELECTORS = (
    '#pane-side',
    'div[aria-label*="Chat list" i]',
    'div[aria-label*="Lista de chats" i]',
    'div[role="application"]',
    'button[aria-label*="Chats" i]',
    'button[aria-label*="Status" i]',
    'button[aria-label*="Estados" i]',
)
_WHATSAPP_STATUS_LABELS = (
    "Add status", "Añadir estado", "Agregar estado", "New status",
    "Nuevo estado", "Add to my status", "Añadir a mi estado",
    "Create status", "Crear estado",
)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_FILE_INPUTS = ('input[type="file"][accept*="image"]',
                'input[type="file"][accept*="image/*"]',
                'input[type="file"]')
_ADD_MEDIA = (
    "Photo/video", "Photo/Video", "Photos and videos", "Photos & videos",
    "Add photos/videos", "Add photo", "Add media", "Photos", "Attach",
    "Foto/video", "Fotos y videos", "Fotos y vídeos", "Agregar foto",
    "Añadir foto", "Añadir archivo", "Adjuntar",
)
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
    ("whatsapp", "story"): ("Send", "Enviar", "Share", "Compartir",
                               "Post", "Publicar"),
}
_WHATSAPP_SEND_NAME = re.compile(
    r"(?:send|share|enviar|compartir)", re.IGNORECASE)
_WHATSAPP_SEND_SELECTORS = (
    'button[aria-label*="send" i]', 'button[aria-label*="enviar" i]',
    'button[aria-label*="share" i]', 'button[aria-label*="compartir" i]',
    'button[title*="send" i]', 'button[title*="enviar" i]',
    'button[data-testid*="send" i]', 'button:has([data-icon="send"])',
    '[role="button"][aria-label*="send" i]',
    '[role="button"][aria-label*="enviar" i]',
    '[role="button"]:has([data-icon="send"])',
)


def _on_browser_thread(fn, timeout_s: float = 180):
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(max_workers=1,
                                       thread_name_prefix="merlin-social-browser")
    return _EXECUTOR.submit(fn).result(timeout_s)


def _handle_file_chooser(chooser) -> None:
    """Attach the requested image if the site opens a native file picker."""
    global _UPLOAD_DONE
    if not _UPLOAD_HINT:
        return
    try:
        chooser.set_files(_UPLOAD_HINT)
        _UPLOAD_DONE = True
        logger.info("Attached %s through the native file chooser", _UPLOAD_HINT)
    except Exception:
        logger.exception("Could not set the file chooser selection")


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
        page.on("filechooser", _handle_file_chooser)
        _BROWSER[platform] = state
    return state["page"]


def _click_text(scope, labels: tuple[str, ...], timeout_ms: int = 3500) -> bool:
    """Click the first label that exists, without paying a timeout per miss."""
    for label in labels:
        trimmed = label.strip()
        pattern = re.compile(rf"^{re.escape(trimmed)}$", re.I)
        for locator in (scope.get_by_role("button", name=pattern),
                        scope.get_by_role("link", name=pattern),
                        scope.get_by_text(pattern, exact=True)):
            try:
                if locator.count() == 0:
                    continue
                locator.first.click(timeout=min(timeout_ms, 2000))
                return True
            except Exception:
                continue
    return False


def _click_whatsapp_publish(composer) -> bool:
    """Click the status editor's accessible send control, including icon-only buttons."""
    role_getter = getattr(composer, "get_by_role", None)
    if role_getter:
        try:
            buttons = role_getter("button", name=_WHATSAPP_SEND_NAME)
            if buttons.count() and buttons.first.is_visible():
                buttons.first.click(timeout=2000)
                return True
        except Exception:
            pass
    for selector in _WHATSAPP_SEND_SELECTORS:
        try:
            buttons = composer.locator(selector)
            if buttons.count() and buttons.first.is_visible():
                buttons.first.click(timeout=2000)
                return True
        except Exception:
            continue
    return False


def _whatsapp_publish_control_visible(composer) -> bool:
    role_getter = getattr(composer, "get_by_role", None)
    if role_getter:
        try:
            buttons = role_getter("button", name=_WHATSAPP_SEND_NAME)
            if any(buttons.nth(index).is_visible()
                   for index in range(buttons.count())):
                return True
        except Exception:
            pass
    for selector in _WHATSAPP_SEND_SELECTORS:
        try:
            matches = composer.locator(selector)
            if any(matches.nth(index).is_visible()
                   for index in range(matches.count())):
                return True
        except Exception:
            continue
    return False


def _dialog(page):
    dialogs = page.get_by_role("dialog")
    return dialogs.last if dialogs.count() else page


def _attach_image(scope, path: str) -> bool:
    name = Path(path).name.casefold()

    def try_inputs() -> bool:
        for selector in _FILE_INPUTS:
            try:
                inputs = scope.locator(selector)
                for index in range(inputs.count()):
                    locator = inputs.nth(index)
                    locator.set_input_files(path, timeout=5000)
                    attached = locator.evaluate(
                        "(element, expected) => Array.from(element.files || []).some(file => "
                        "file.name.toLowerCase() === expected)", name)
                    if attached:
                        return True
            except Exception:
                continue
        return False

    if try_inputs():
        return True
    # Several web composers create their hidden file input only after the user
    # opens the media picker. Trigger that control, then retry DOM attachment.
    return _click_text(scope, _ADD_MEDIA, timeout_ms=1800) and try_inputs()


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


def _has_any(page, selectors) -> bool:
    for selector in selectors:
        try:
            if page.locator(selector).count():
                return True
        except Exception:
            continue
    return False


def _logged_out(page, platform: str) -> bool:
    url = page.url.casefold()
    if platform == "facebook":
        return any(marker in url for marker in ("login", "signin", "checkpoint"))
    # WhatsApp keeps web.whatsapp.com in the URL on the QR/login screen, so the
    # URL alone cannot tell a signed-in session from a signed-out one.
    if "web.whatsapp.com" not in url or "login" in url:
        return True
    deadline = time.time() + 20
    while time.time() < deadline:
        if _has_any(page, _WHATSAPP_READY_SELECTORS):
            return False
        if _has_any(page, _WHATSAPP_QR_SELECTORS):
            return True
        page.wait_for_timeout(500)
    return True


def _debug_shot(page, tag: str) -> str:
    """Save what the social site showed on failure, for diagnosis."""
    try:
        _SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        path = _SCREENSHOTS / f"social_debug_{tag}.png"
        page.screenshot(path=str(path))
        logger.info("social debug screenshot saved to %s", path)
        return str(path)
    except Exception:
        return ""


def _compose_caption(platform: str, mode: str, instruction: str,
                     language: str = "es") -> str:
    """Draft platform-appropriate copy without pretending to see the image."""
    is_es = str(language).lower().startswith("es")
    story = mode == "story"
    tone = ("casual, warm and very short, like a relaxed story caption" if story
            else "friendly, conversational and concise for a Facebook feed post")
    generic = ("Write a generic caption for sharing a recent moment. The image "
               "is attached but has not been analyzed." if is_es else
               "Write a generic caption for sharing a recent moment. The attached "
               "image has not been analyzed.")
    fallback = (("Un momento para compartir ✨" if story else
                 "Qué gusto compartir este momento. ¡Seguimos adelante! ✨")
                if is_es else
                ("A little moment to share ✨" if story else
                 "Glad to share this moment. On to the next step! ✨"))
    try:
        from merlin.llm import get_llm

        answer = get_llm("fast").chat(
            [{"role": "system", "content": (
                f"Write in {'Spanish' if is_es else 'English'}. Create {tone}. "
                "Use at most one emoji, no hashtags for stories, and no invented "
                "facts. Do not describe image contents: you cannot see the image. "
                "Return only the caption, at most 25 words.")},
             {"role": "user", "content": (
                 f"Platform: {platform}. Request: {instruction.strip() or generic}")}],
            fallback=fallback,
        )
        answer = re.sub(r"\s+", " ", (answer or "")).strip().strip('"“”')
        return answer[:280] or fallback
    except Exception:
        logger.exception("Social caption generation failed; using safe fallback")
        return fallback


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
        if not _click_text(page, _STORY_BUTTONS[platform], timeout_ms=5000):
            return False
        page.wait_for_timeout(1500)
        if not _click_text(page, _WHATSAPP_STATUS_LABELS, timeout_ms=2500):
            return False
        page.wait_for_timeout(800)
        # The "+" control opens a small menu (not a dialog) with
        # "Photos & videos" / "Text". Picking a photo opens the native file
        # picker, which _handle_file_chooser fills with the staged image.
        _click_text(page, ("Photos & videos", "Fotos y videos",
                           "Fotos y vídeos", "Photo and video",
                           "Photo & video", "Foto y video"), timeout_ms=2500)
        page.wait_for_timeout(1500)
        return True
    return False


def _stage(platform: str, mode: str, text: str, file: str | None,
           stage_id: str) -> dict:
    with browser_ui.agent_mode():
        return _stage_impl_social(platform, mode, text, file, stage_id)


def _stage_impl_social(platform: str, mode: str, text: str, file: str | None,
                       stage_id: str) -> dict:
    page = _page(platform)
    url = "https://www.facebook.com/" if platform == "facebook" else \
        "https://web.whatsapp.com/"
    browser_ui.announce(page, f"Abriendo {platform.title()}…")
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    if _logged_out(page, platform):
        shot = _debug_shot(page, f"{platform}_login")
        detail = f" Debug: {shot}" if shot else ""
        return {"ok": False, "need_login": True,
                "error": f"Sign in to {platform.title()} in the opened browser "
                         f"first.{detail}"}

    browser_ui.announce(page, f"Preparando tu {mode} en {platform.title()}…")
    if not _open_composer(page, platform, mode):
        shot = _debug_shot(page, f"{platform}_{mode}_composer")
        detail = f" Debug: {shot}" if shot else ""
        return {"ok": False,
                "error": f"Could not open the {platform} {mode} composer.{detail}"}
    page.wait_for_timeout(1500)
    if platform == "whatsapp" and mode == "story":
        # The native picker opens asynchronously; give it a moment to hand the
        # image to WhatsApp before deciding whether the composer is safe.
        deadline = time.time() + 5
        while time.time() < deadline and not _UPLOAD_DONE:
            page.wait_for_timeout(300)
    chooser_upload = bool(_UPLOAD_DONE)
    if page.get_by_role("dialog").count():
        composer = _dialog(page)
    elif platform == "whatsapp" and mode == "story" and chooser_upload:
        # The status preview is a full-screen view, not a role=dialog. Only
        # trust it after WhatsApp's own file picker accepted our image; never
        # treat a bare page as a composer.
        composer = page
    else:
        shot = _debug_shot(page, f"{platform}_{mode}_no_dialog")
        detail = f" Debug: {shot}" if shot else ""
        return {"ok": False,
                "error": f"Could not identify a safe {platform} composer dialog; "
                         f"no content was entered.{detail}"}

    if file and not chooser_upload and not _file_is_still_attached(
            composer, Path(file).name):
        # Some sites portal the hidden upload input outside the dialog subtree.
        # Search there only after the scoped composer lookup; _attach_image checks
        # the selected filename before declaring success.
        browser_ui.announce(page, "Adjuntando la captura…")
        if not (_attach_image(composer, file) or
                (composer is not page and _attach_image(page, file))):
            shot = _debug_shot(page, f"{platform}_{mode}_attach")
            detail = f" Debug: {shot}" if shot else ""
            return {"ok": False,
                    "error": f"Could not attach the image to the {platform} "
                             f"composer.{detail}"}
    entered_text = ""
    if text and composer is not page:
        editors = composer.locator('[contenteditable="true"], textarea, [role="textbox"]')
        try:
            editor = editors.first
            editor.wait_for(timeout=5000)
            editor.fill(text, timeout=5000)
            entered_text = text
        except Exception:
            if mode == "story" and file:
                # Story/status composers support image-only updates.
                pass
            else:
                return {"ok": False, "error": f"Could not enter text in the {platform} composer."}
    browser_ui.announce(page, "Publicación lista para revisar.")

    _STAGED[platform] = {"id": stage_id, "mode": mode, "page": page,
                         "text": entered_text,
                         "overlay": composer is page,
                         "chooser_upload": chooser_upload,
                         "file_name": Path(file).name if file else ""}
    return {"ok": True, "staged": True, "needs_confirmation": True,
            "stage_id": stage_id, "platform": platform, "mode": mode,
            "text": entered_text, "file": file, "method": "playwright"}


def _publish(platform: str, mode: str, stage_id: str) -> dict:
    with browser_ui.agent_mode():
        return _publish_impl_social(platform, mode, stage_id)


def _publish_impl_social(platform: str, mode: str, stage_id: str) -> dict:
    staged = _STAGED.get(platform)
    if not staged or staged.get("id") != stage_id or staged.get("mode") != mode:
        return {"ok": False, "error": "The staged post expired or no longer matches this confirmation."}
    page = staged["page"]
    has_dialog = bool(page.get_by_role("dialog").count())
    if not has_dialog and not staged.get("overlay"):
        _STAGED.pop(platform, None)
        return {"ok": False, "error": "The reviewed composer is no longer open; nothing was published."}
    composer = _dialog(page) if has_dialog else page
    if not staged.get("chooser_upload") and not (
            _file_is_still_attached(composer, staged.get("file_name", "")) or
            _file_is_still_attached(page, staged.get("file_name", ""))):
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
    browser_ui.announce(page, f"Publicando en {platform.title()}…")
    published_click = (
        _click_whatsapp_publish(composer)
        if platform == "whatsapp" and mode == "story"
        else _click_text(composer, _PUBLISH_BUTTONS.get((platform, mode), ()),
                         timeout_ms=5000)
    )
    if not published_click:
        return {"ok": False, "error": f"Could not find the {platform} {mode} publish button in the open composer."}
    page.wait_for_timeout(2500)
    if has_dialog and page.get_by_role("dialog").count():
        return {"ok": False, "error": f"The composer dialog did not close after clicking publish; publication may have failed."}
    if (platform == "whatsapp" and mode == "story" and staged.get("overlay")
            and _whatsapp_publish_control_visible(composer)):
        return {"ok": False,
                "error": "WhatsApp still shows the status send control; publication was not confirmed."}
    _STAGED.pop(platform, None)
    return {"ok": True, "published": True, "platform": platform,
            "mode": mode, "method": "playwright"}


def publish_social(platform: str, mode: str, text: str = "",
                   file_path: str = "", publish: bool = False,
                   stage_id: str = "", language: str = "es") -> dict:
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
    if file and not text.strip():
        text = _compose_caption(platform, mode, text, language)
    token = uuid.uuid4().hex
    global _UPLOAD_HINT, _UPLOAD_DONE
    _UPLOAD_HINT = str(file) if file else None
    _UPLOAD_DONE = False
    try:
        return _on_browser_thread(lambda: _stage(platform, mode, text,
                                                 str(file) if file else None, token))
    except Exception as exc:
        logger.exception("Social publication staging failed")
        return {"ok": False, "error": str(exc)}
    finally:
        _UPLOAD_HINT = None
