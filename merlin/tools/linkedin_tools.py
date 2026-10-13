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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logger = logging.getLogger("merlin.tools.linkedin")

_PROFILE = Path.home() / "merlin" / "browser_profiles" / "linkedin"
_SCREENSHOTS = Path.home() / "merlin" / "screenshots"

_BROWSER_EXEC: ThreadPoolExecutor | None = None   # single owner of Playwright

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
    'div[role="button"][aria-label*="Add photo"]',
    'div[role="button"][aria-label*="Agregar foto"]',
    'button:has-text("Add photo")',
    'button:has-text("Agregar foto")',
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
    "You write short, authentic LinkedIn posts for a developer. Reply in the "
    "same language as the request. 3-6 short lines, energetic but human tone, "
    "at most 2 emojis and 3 hashtags, no corporate buzzwords, no "
    "'excited to announce'. Output ONLY the post text."
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
    return state["page"]


def _resolve_image(image_path: str) -> str | None:
    """'last'/'' -> newest screenshot in ~/merlin/screenshots, if any."""
    if not image_path or image_path.strip().lower() in ("last", "última",
                                                        "ultima", "latest"):
        files = sorted(_SCREENSHOTS.glob("merlin_*.png")) if _SCREENSHOTS.exists() else []
        return str(files[-1]) if files else None
    p = Path(image_path).expanduser()
    return str(p) if p.exists() else None


def _compose_draft(instruction: str) -> str:
    from merlin.llm import get_llm

    answer = get_llm("fast").chat(
        [{"role": "system", "content": _COMPOSE_SYSTEM},
         {"role": "user",
          "content": f"Write a LinkedIn post. Request: {instruction}"}],
        fallback="",
    )
    answer = re.sub(r"\*\*|__|`|^#+\s*", "", answer or "", flags=re.MULTILINE)
    answer = answer.strip()[:1300]
    if answer:
        return answer
    # Offline fallback: a decent template instead of failing the whole post
    logger.warning("LLM draft failed; using the local template")
    return (f"{instruction.strip(' .').capitalize()}.\n"
            f"Seguimos construyendo MERLIN, mi asistente local de IA. "
            f"#AI #LocalAI #DevLife")


def _click_first(page, selectors, timeout_ms: int = 4000, what: str = "button"):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    logger.debug("no %s found among selectors", what)
    return False


def _attach_image(page, image: str) -> bool:
    """Attach the image in the open composer modal (input first, then the
    Add-photo button as fallback)."""
    for sel in _IMAGE_INPUTS:
        try:
            page.locator(sel).first.set_input_files(image, timeout=5000)
            page.wait_for_timeout(3500)  # let the preview render
            return True
        except Exception:
            continue
    if _click_first(page, _ADD_PHOTO, what="add-photo button"):
        for sel in _IMAGE_INPUTS:
            try:
                page.locator(sel).first.set_input_files(image, timeout=5000)
                page.wait_for_timeout(3500)
                return True
            except Exception:
                continue
    logger.warning("could not attach image to the LinkedIn composer")
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


def _stage_impl(text: str, image: str | None) -> dict:
    page = _get_page()
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded",
              timeout=45000)
    page.wait_for_timeout(4000)
    url = page.url.lower()
    if any(s in url for s in ("/login", "/checkpoint", "signin")):
        _debug_shot(page, "login")
        return {"ok": False, "need_login": True,
                "note": "Inició sesión en la ventana del navegador abierta "
                        "(una sola vez); luego vuelve a pedírmelo."}

    if not _click_first(page, _START_POST, what="start-post button"):
        _debug_shot(page, "no_start_post")
        return {"ok": False,
                "error": "No encontré el botón 'Start a post' de LinkedIn "
                         "(screenshot de depuración en tus capturas)."}
    page.wait_for_timeout(2000)

    editor = _wait_editor(page)
    if editor is None:
        _debug_shot(page, "no_editor")
        return {"ok": False,
                "error": "El composer de LinkedIn no abrió a tiempo "
                         "(screenshot de depuración en tus capturas)."}

    # image FIRST: attaching can steal focus and the editor keeps working after
    if image:
        if not _attach_image(page, image):
            _debug_shot(page, "no_attach")
            return {"ok": False,
                    "error": "No pude adjuntar la imagen al composer de LinkedIn "
                             "(screenshot de depuración en tus capturas)."}

    try:
        editor.click()
        if text:
            editor.type(text, delay=15)
    except Exception as exc:
        _debug_shot(page, "no_typing")
        return {"ok": False, "error": f"No pude escribir el texto: {exc}"}

    return {"ok": True, "staged": True, "needs_confirmation": True,
            "draft": text, "image": image, "method": "playwright"}


def _publish_impl() -> dict:
    """The staged composer is still open with the draft: just press Post."""
    page = _get_page()
    if not _click_first(page, _PUBLISH, timeout_ms=8000,
                        what="publish button"):
        return {"ok": False,
                "error": "No encontré el composer abierto con el borrador. "
                         "Vuelve a pedírmelo y revisa el borrador de nuevo."}
    page.wait_for_timeout(4000)
    return {"ok": True, "published": True, "method": "playwright"}


def post_linkedin(instruction: str = "", text: str = "", image_path: str = "",
                  publish: bool = False) -> dict:
    """Stage (default) or publish a LinkedIn post. See the module docstring."""
    try:
        if publish:
            return _on_browser_thread(_publish_impl)

        if not (text or "").strip():
            text = _compose_draft(instruction or "share my latest dev progress")
            if not text:
                return {"ok": False,
                        "error": "No pude redactar el post (¿está el LLM activo?)"}
        text = (text or "").strip()
        image = _resolve_image(image_path)
        result = _on_browser_thread(lambda: _stage_impl(text, image))
        if result.get("ok"):
            result["draft"] = text
        return result
    except Exception as exc:
        logger.exception("post_linkedin failed")
        return {"ok": False, "error": str(exc)}
