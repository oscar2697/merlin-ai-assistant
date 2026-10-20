"""LinkedIn publishing via Playwright with a persistent browser profile.

Two-phase, human-in-the-loop (as in the demo video):

  post_linkedin(instruction, image_path="")     -> composes the text with the
      fast LLM, opens LinkedIn's composer with text (+ image) filled in and
      returns {"staged": True, "draft": text, "needs_confirmation": True}.
      The composer stays OPEN on screen for the user to review.
  post_linkedin(..., publish=True)             -> clicks "Post" in the
      still-open composer (the os_agent only reaches here after a spoken
      "sí" to the confirmation question).

First run: the browser opens on the LinkedIn login page - log in once
(with "Remember me") and the session persists in ~/merlin/browser_profiles/
linkedin. Later runs reuse it automatically.

Threading: the Playwright SYNC API is bound to the thread that creates its
objects, but every MERLIN turn runs on a new graph thread - so all browser
work is funnelled through a single dedicated worker thread (_BROWSER_EXEC).
This fixes "cannot switch to a different thread (which happens to have exited)".

WARNING: LinkedIn detects automation and can restrict accounts. Use it
sparingly, always with the spoken confirmation, and with human-sounding text.
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

logger = logging.getLogger("merlin.tools.linkedin")

_PROFILE = Path.home() / "merlin" / "browser_profiles" / "linkedin"
_SCREENSHOTS = Path.home() / "merlin" / "screenshots"

_BROWSER_EXEC: ThreadPoolExecutor | None = None   # single owner of Playwright
_STAGED_POST: dict = {}
_UPLOAD_HINT: str | None = None
_UPLOAD_DONE = False


def _handle_file_chooser(chooser) -> None:
    """Feed LinkedIn's native picker when its composer opens one."""
    global _UPLOAD_DONE
    if not _UPLOAD_HINT:
        return
    try:
        chooser.set_files(_UPLOAD_HINT)
        _UPLOAD_DONE = True
        logger.info("LinkedIn accepted the file chooser selection: %s",
                    Path(_UPLOAD_HINT).name)
    except Exception:
        logger.exception("Could not set the LinkedIn file chooser selection")

# Selector fallbacks (LinkedIn changes its DOM, uses div[role=button] and the
# UI language varies)
_START_POST = (
    'div[role="button"][aria-label*="Start a post"]',
    'div[role="button"][aria-label*="Crear una publicación"]',
    'div[role="button"]:has-text("Start a post")',
    'div[role="button"]:has-text("Crear una publicación")',
    'button[aria-label*="Start a post"]',
    'button[aria-label*="Crear una publicación"]',
    'button:has-text("Start a post")',
    'button:has-text("Crear una publicación")',
)
_EDITOR = (
    '.ql-editor[contenteditable="true"]',
    'div.ql-editor',
    'div[role="textbox"][aria-label*="exto"]',
    'div[role="textbox"][aria-label*="ext box"]',
    'div[role="textbox"]',
)
_IMAGE_INPUTS = (
    'input[type="file"][accept*="image"]',
    'input[name="file_upload"]',
    'input[accept*="image"][type="file"]',
    'input[type="file"]',
)
_ADD_PHOTO = (
    'button[aria-label*="photo" i]',
    'button[aria-label*="image" i]',
    'button[aria-label*="media" i]',
    'button[title*="photo" i]',
    'button[title*="image" i]',
    'button[title*="media" i]',
    'div[role="button"][aria-label*="photo" i]',
    'div[role="button"][aria-label*="image" i]',
    'div[role="button"][aria-label*="media" i]',
    'div[role="button"][aria-label*="Add photo"]',
    'div[role="button"][aria-label*="Add media"]',
    'div[role="button"][aria-label*="Add a photo"]',
    'div[role="button"][aria-label*="Agregar foto"]',
    'div[role="button"][aria-label*="Añadir foto"]',
    'div[role="button"][aria-label*="Añadir medios"]',
    'div[role="button"][aria-label*="contenido multimedia"]',
    'div[role="button"][aria-label*="imagen"]',
    'button[aria-label*="Add photo"]',
    'button[aria-label*="Add media"]',
    'button[aria-label*="Agregar foto"]',
    'button[aria-label*="Añadir foto"]',
    'button[aria-label*="contenido multimedia"]',
    'button[aria-label*="imagen"]',
    'button:has-text("Add photo")',
    'button:has-text("Add media")',
    'button:has-text("Add a photo")',
    'button:has-text("Agregar foto")',
    'button:has-text("Añadir foto")',
    'button:has-text("Añadir medios")',
    'button:has-text("Contenido multimedia")',
    'button:has-text("Imagen")',
)
_PUBLISH = (
    'button:has-text("Post")',
    'button:has-text("Publicar")',
    'div[role="button"]:has-text("Post")',
    'div[role="button"]:has-text("Publicar")',
    'button.share-actions__primary-button',
    'button[type="submit"]',
)

_COMPOSE_SYSTEM = (
    "Write a concise, professional and credible LinkedIn post in the requested "
    "language. Use 3-6 short lines, a clear point and a human tone. Avoid "
    "corporate clichés, unsupported claims, invented accomplishments, and more "
    "than 2 emojis or 3 hashtags. Do not claim to have inspected an attached "
    "image; use only the user's text. Output ONLY the post text."
)


def _on_browser_thread(fn, timeout_s: float = 180):
    """Run `fn` on the single browser-owner thread and return its result.

    Playwright sync objects must never cross threads; this executor is the
    only place they are created and used."""
    global _BROWSER_EXEC
    if _BROWSER_EXEC is None:
        _BROWSER_EXEC = ThreadPoolExecutor(max_workers=1,
                                           thread_name_prefix="merlin-browser")
    return _BROWSER_EXEC.submit(fn).result(timeout_s)


def _get_page():
    """Singleton: a headed persistent Chromium context (the user can watch it).
    MUST be called on the browser thread (via _on_browser_thread)."""
    from playwright.sync_api import sync_playwright

    if not hasattr(_get_page, "state"):
        state = {"pw": None, "browser": None, "page": None}
        _get_page.state = state
    state = _get_page.state
    if state["page"] is None:
        _PROFILE.mkdir(parents=True, exist_ok=True)
        state["pw"] = sync_playwright().start()
        state["browser"] = state["pw"].chromium.launch_persistent_context(
            str(_PROFILE), headless=False,
            viewport={"width": 1280, "height": 860})
        state["page"] = (state["browser"].pages[0]
                         if state["browser"].pages
                         else state["browser"].new_page())
        state["page"].on("filechooser", _handle_file_chooser)
    return state["page"]


def _resolve_image(image_path: str) -> str | None:
    """Resolve an image path or screenshot alias accepted by LinkedIn."""
    path = resolve_file(image_path)
    if path is None or path.suffix.casefold() not in {
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return None
    return str(path)


def _compose_draft(instruction: str, language: str = "es") -> str:
    from merlin.llm import get_llm

    is_es = str(language).lower().startswith("es")
    fallback = (
        "Comparto una actualización sobre mi trabajo reciente. "
        "Cada avance también es una oportunidad para aprender, mejorar y seguir construyendo."
        if is_es else
        "A brief update on my recent work. Every step is an opportunity to learn, "
        "improve, and keep building."
    )
    answer = get_llm("fast").chat(
        [{"role": "system", "content": _COMPOSE_SYSTEM},
         {"role": "user",
          "content": f"Requested language: {'Spanish' if is_es else 'English'}\n"
                     f"Write a LinkedIn post based only on this request: {instruction}"}],
        fallback=fallback,
    )
    answer = re.sub(r"\*\*|__|`|^#+\s*", "", answer or "", flags=re.MULTILINE)
    answer = answer.strip()[:1300]
    return answer or fallback


def _click_first(page, selectors, timeout_ms: int = 4000, what: str = "button"):
    for sel in selectors:
        try:
            matches = page.locator(sel)
            if matches.count() == 0:
                continue
            loc = matches.first
            if not loc.is_visible():
                continue
            loc.click(timeout=min(timeout_ms, 1800))
            return True
        except Exception:
            continue
    logger.debug("no %s found among selectors", what)
    return False


_START_POST_ROLE_RE = re.compile(
    r"(?:start|create|empezar|crear|comenzar)\s+(?:a\s+|una\s+)?"
    r"(?:post|publicaci[oó]n)", re.IGNORECASE)


def _click_start_post(page, deadline_s: float = 20.0) -> bool:
    """Wait for LinkedIn's share box and open the composer.

    Role-based locators survive LinkedIn's DOM churn; the CSS list only runs
    after them. The share trigger is often rendered later than the feed, so keep
    polling instead of failing on the first pass.
    """
    deadline = time.time() + deadline_s
    while True:
        candidates = [
            page.get_by_role("button", name=_START_POST_ROLE_RE),
            page.get_by_role("link", name=_START_POST_ROLE_RE),
            page.locator('[aria-label*="Start a post"]'),
            page.locator('[aria-label*="Crear una publicación"]'),
            page.locator(".share-box-feed-entry__trigger"),
        ]
        candidates.extend(page.locator(sel) for sel in _START_POST)
        for locator in candidates:
            try:
                if locator.count() == 0:
                    continue
                item = locator.first
                if not item.is_visible():
                    continue
                try:
                    item.click(timeout=2500)
                except Exception:
                    item.click(timeout=2500, force=True)
                return True
            except Exception:
                continue
        if time.time() >= deadline:
            return False
        page.wait_for_timeout(500)


def _attach_image(page, image: str) -> bool:
    """Attach the image in the open composer modal (input first, then the
    Add-photo button as fallback). Confirm the browser accepted this exact file."""
    expected = Path(image).name.casefold()

    try:
        dialogs = page.get_by_role("dialog")
        composer = dialogs.last if dialogs.count() else page
    except Exception:
        composer = page

    def try_inputs(scope) -> bool:
        for sel in _IMAGE_INPUTS:
            try:
                locators = scope.locator(sel)
                for index in range(locators.count()):
                    locator = locators.nth(index)
                    locator.set_input_files(image, timeout=1500)
                    attached = locator.evaluate(
                        "(element, name) => Array.from(element.files || []).some("
                        "file => file.name.toLowerCase() === name)", expected)
                    if attached:
                        page.wait_for_timeout(1500)
                        return True
            except Exception:
                continue
        return False

    if try_inputs(composer) or (composer is not page and try_inputs(page)):
        return True
    clicked_media = False
    try:
        media_button = composer.get_by_role(
            "button", name=re.compile(r"(?:photo|image|media|foto|imagen|multimedia)", re.I))
        if media_button.count() and media_button.first.is_visible():
            media_button.first.click(timeout=1800)
            clicked_media = True
    except Exception:
        pass
    if clicked_media or _click_first(composer, _ADD_PHOTO,
                                    what="add-photo button"):
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if _UPLOAD_DONE:
                return True
            if (try_inputs(composer) or
                    (composer is not page and try_inputs(page))):
                return True
            page.wait_for_timeout(250)
    logger.warning("could not attach image to the LinkedIn composer")
    return False


def _image_is_attached(page, filename: str) -> bool:
    """Check that an upload input still contains the staged image."""
    expected = Path(filename).name.casefold()
    for selector in _IMAGE_INPUTS:
        try:
            inputs = page.locator(selector)
            for index in range(inputs.count()):
                if inputs.nth(index).evaluate(
                        "(element, name) => Array.from(element.files || []).some("
                        "file => file.name.toLowerCase() === name)", expected):
                    return True
        except Exception:
            continue
    return False


def _debug_shot(page, tag: str) -> None:
    """Save what LinkedIn showed when something failed, for diagnosis."""
    try:
        _SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        path = _SCREENSHOTS / f"linkedin_debug_{tag}.png"
        page.screenshot(path=str(path))
        logger.info("debug screenshot saved to %s", path)
    except Exception:
        pass


def _wait_editor(page, timeout_s: float = 12.0):
    """Retry the composer modal editor for a while (LinkedIn is slow to open)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for sel in _EDITOR:
            try:
                loc = page.locator(sel).first
                loc.wait_for(timeout=800)
                return loc
            except Exception:
                continue
        page.wait_for_timeout(500)
    return None


def _stage_impl(text: str, image: str | None, stage_id: str) -> dict:
    page = _get_page()
    browser_ui.announce(page, "Abriendo LinkedIn…")
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded",
              timeout=45000)
    page.wait_for_timeout(4000)
    url = page.url.lower()
    if any(s in url for s in ("/login", "/checkpoint", "signin")):
        _debug_shot(page, "login")
        return {"ok": False, "need_login": True,
                "note": "Inició sesión en la ventana del navegador abierta "
                        "(una sola vez); luego vuelve a pedírmelo."}

    browser_ui.announce(page, "Abriendo el editor de LinkedIn…",
                        selector=(".share-box-feed-entry__trigger",
                                  '[aria-label*="Start a post"]',
                                  'div[role="button"][aria-label*="Start a post"]'))
    editor = None
    if _click_start_post(page):
        page.wait_for_timeout(1500)
        editor = _wait_editor(page)
    if editor is None:
        # LinkedIn also opens the share composer directly from this URL, which
        # keeps the flow working when the feed button is hidden or restyled.
        logger.info("LinkedIn share box not found; opening the direct composer URL")
        try:
            page.goto("https://www.linkedin.com/feed/?shareActive=true",
                      wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
        except Exception:
            logger.debug("LinkedIn direct composer URL failed", exc_info=True)
        editor = _wait_editor(page, timeout_s=8.0)
    if editor is None:
        _debug_shot(page, "no_start_post")
        return {"ok": False,
                "error": "No encontré el botón 'Start a post' de LinkedIn "
                         "(screenshot de depuración en tus capturas)."}

    # image FIRST: attaching can steal focus and the editor keeps working after
    if image:
        browser_ui.announce(page, "Adjuntando la captura…")
        if not _attach_image(page, image):
            _debug_shot(page, "no_attach")
            return {"ok": False,
                    "error": "No pude adjuntar la imagen al composer de LinkedIn "
                             "(screenshot de depuración en tus capturas)."}

    browser_ui.announce(page, "Redactando tu publicación…")
    try:
        editor.click()
        if text:
            editor.type(text, delay=15)
    except Exception as exc:
        _debug_shot(page, "no_typing")
        return {"ok": False, "error": f"No pude escribir el texto: {exc}"}
    browser_ui.announce(page, "Publicación lista para revisar.")

    _STAGED_POST.clear()
    _STAGED_POST.update({"stage_id": stage_id, "text": text,
                         "image": image, "page": page,
                         "chooser_upload": bool(_UPLOAD_DONE)})
    return {"ok": True, "staged": True, "needs_confirmation": True,
            "stage_id": stage_id, "draft": text, "image": image,
            "method": "playwright"}


def _publish_impl(stage_id: str) -> dict:
    """Publish only the exact composer that MERLIN staged for confirmation."""
    staged = _STAGED_POST
    if not stage_id or staged.get("stage_id") != stage_id:
        return {"ok": False,
                "error": "The staged LinkedIn post expired or no longer matches this confirmation."}
    page = staged.get("page")
    if page is None:
        return {"ok": False, "error": "The staged LinkedIn composer is unavailable."}
    editor = _wait_editor(page, timeout_s=2.0)
    if editor is None:
        return {"ok": False,
                "error": "The staged LinkedIn composer is no longer open; nothing was published."}
    try:
        current_text = " ".join(editor.inner_text(timeout=1500).split())
    except Exception:
        return {"ok": False,
                "error": "Could not verify the reviewed LinkedIn draft; nothing was published."}
    if current_text != " ".join(staged["text"].split()):
        return {"ok": False,
                "error": "The LinkedIn draft changed after review; nothing was published."}
    image = staged.get("image")
    if (image and not staged.get("chooser_upload")
            and not _image_is_attached(page, Path(image).name)):
        return {"ok": False,
                "error": "The staged LinkedIn image is no longer attached; nothing was published."}
    browser_ui.announce(page, "Publicando en LinkedIn…")
    if not _click_first(page, _PUBLISH, timeout_ms=8000,
                        what="publish button"):
        return {"ok": False,
                "error": "No encontré el composer abierto con el borrador. "
                         "Vuelve a pedírmelo y revisa el borrador de nuevo."}
    page.wait_for_timeout(4000)
    _STAGED_POST.clear()
    return {"ok": True, "published": True, "method": "playwright"}


def post_linkedin(instruction: str = "", text: str = "", image_path: str = "",
                  publish: bool = False, file_path: str = "",
                  stage_id: str = "", language: str = "es") -> dict:
    """Stage (default) or publish a LinkedIn post. See the module docstring."""
    try:
        if publish:
            if not stage_id:
                return {"ok": False,
                        "error": "A matching staged LinkedIn post is required before publishing."}
            with browser_ui.agent_mode():
                return _on_browser_thread(lambda: _publish_impl(stage_id))

        if not (text or "").strip():
            text = _compose_draft(
                instruction or "Comparte una actualización profesional sobre mi trabajo reciente.",
                language)
            if not text:
                return {"ok": False,
                        "error": "No pude redactar el post (¿está el LLM activo?)"}
        text = (text or "").strip()
        requested_file = file_path or image_path
        image = _resolve_image(requested_file)
        if requested_file and image is None:
            return {"ok": False,
                    "error": "The attachment is missing or is not a supported image. "
                             "LinkedIn posts currently accept image attachments only."}
        token = uuid.uuid4().hex
        global _UPLOAD_HINT, _UPLOAD_DONE
        _UPLOAD_HINT = image
        _UPLOAD_DONE = False
        try:
            with browser_ui.agent_mode():
                result = _on_browser_thread(lambda: _stage_impl(text, image, token))
        finally:
            _UPLOAD_HINT = None
        if result.get("ok"):
            result["draft"] = text
        return result
    except Exception as exc:
        logger.exception("post_linkedin failed")
        return {"ok": False, "error": str(exc)}
