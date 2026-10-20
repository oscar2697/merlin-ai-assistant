"""Word-write reporting must fail closed when clipboard verification fails."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.tools import writing_tools


def _pyautogui_stub():
    return SimpleNamespace(hotkey=lambda *_args: None, press=lambda *_args: None)


def test_verification_exception_is_not_reported_as_success():
    text = "A generated essay that should be visible in the Word document."
    with patch.dict(sys.modules, {"pyautogui": _pyautogui_stub()}), \
         patch.object(writing_tools, "_write_word_com", return_value=None), \
         patch.object(writing_tools, "focus_window",
                      return_value={"ok": True, "window": "Document1 - Word"}), \
         patch.object(writing_tools, "_wait_for_document_window",
                      return_value={"ok": True, "window": "Document1 - Word"}), \
         patch.object(writing_tools, "_clipboard_get",
                      side_effect=["old clipboard", RuntimeError("clipboard unavailable")]), \
         patch.object(writing_tools, "_clipboard_set"):
        result = writing_tools.type_into_app("Word", text, create_new_document=True)
    assert result["ok"] is False
    assert result["verified"] is False
    assert result["pasted_attempted"] is True


def test_matching_visible_text_is_reported_as_verified():
    text = "A generated essay that should be visible in the Word document."
    with patch.dict(sys.modules, {"pyautogui": _pyautogui_stub()}), \
         patch.object(writing_tools, "_write_word_com", return_value=None), \
         patch.object(writing_tools, "focus_window",
                      return_value={"ok": True, "window": "Document1 - Word"}), \
         patch.object(writing_tools, "_wait_for_document_window",
                      return_value={"ok": True, "window": "Document1 - Word"}), \
         patch.object(writing_tools, "_clipboard_get",
                      side_effect=["old clipboard", text]), \
         patch.object(writing_tools, "_clipboard_set"):
        result = writing_tools.type_into_app("Word", text, create_new_document=True)
    assert result["ok"] is True
    assert result["verified"] is True


def test_word_home_selects_blank_template_before_pasting():
    text = "Generated text in the actual document."
    pressed = []
    fake_focus = {"ok": True, "window": "Document1 - Word"}
    with patch.dict(sys.modules, {"pyautogui": SimpleNamespace(
            hotkey=lambda *_args: None, press=lambda key: pressed.append(key))}), \
         patch.object(writing_tools, "_write_word_com", return_value=None), \
         patch.object(writing_tools, "focus_window",
                      return_value={"ok": True, "window": "Word"}), \
         patch.object(writing_tools, "_wait_for_template_gallery",
                      return_value=True), \
         patch.object(writing_tools, "_wait_for_document_window",
                      return_value=fake_focus), \
         patch.object(writing_tools, "_clipboard_get",
                      side_effect=["old clipboard", text]), \
         patch.object(writing_tools, "_clipboard_set"):
        result = writing_tools.type_into_app("Word", text, create_new_document=True)
    assert "enter" in pressed
    assert result["ok"] is True and result["verified"] is True


def test_home_or_loading_titles_are_not_documents():
    assert not writing_tools._is_document_window("Word", "Word")
    assert not writing_tools._is_document_window("Opening - Word", "Word")
    assert not writing_tools._is_document_window("Home - Word", "Word")
    assert writing_tools._is_document_window("Document1 - Word", "Word")


def test_word_com_creates_and_verifies_document():
    content = SimpleNamespace(Text="")
    document = SimpleNamespace(Name="Document1", Content=content,
                                Activate=lambda: None)
    app = SimpleNamespace(Visible=False,
                          Documents=SimpleNamespace(Add=lambda: document),
                          Activate=lambda: None)
    client = types.ModuleType("win32com.client")
    client.GetActiveObject = lambda _name: app
    client.Dispatch = lambda _name: app
    win32com = types.ModuleType("win32com")
    win32com.__path__ = []
    win32com.client = client
    pythoncom = types.ModuleType("pythoncom")
    pythoncom.CoInitialize = lambda: None
    pythoncom.CoUninitialize = lambda: None
    with patch.dict(sys.modules, {"pythoncom": pythoncom,
                                  "win32com": win32com,
                                  "win32com.client": client}):
        result = writing_tools._write_word_com("Ensayo de prueba.")
    assert result["ok"] is True and result["verified"] is True
    assert content.Text == "Ensayo de prueba."
    assert app.Visible is True


def test_stale_expected_clipboard_cannot_verify_empty_document():
    text = "Generated text still on the clipboard from the paste step."
    clipboard = {"value": text}

    def hotkey(*keys):
        # Simulate Ctrl+C not replacing the clipboard because Word's Home
        # screen, rather than a document, has focus.
        return None

    def clipboard_set(value):
        clipboard["value"] = value

    with patch.dict(sys.modules, {"pyautogui": SimpleNamespace(
            hotkey=hotkey, press=lambda *_args: None)}), \
         patch.object(writing_tools, "_write_word_com", return_value=None), \
         patch.object(writing_tools, "focus_window",
                      return_value={"ok": True, "window": "Document1 - Word"}), \
         patch.object(writing_tools, "_wait_for_document_window",
                      return_value={"ok": True, "window": "Document1 - Word"}), \
         patch.object(writing_tools, "_clipboard_get",
                      side_effect=lambda: clipboard["value"]), \
         patch.object(writing_tools, "_clipboard_set", side_effect=clipboard_set):
        result = writing_tools.type_into_app("Word", text, create_new_document=True)
    assert result["ok"] is False
    assert result["verified"] is False


if __name__ == "__main__":
    test_verification_exception_is_not_reported_as_success()
    test_matching_visible_text_is_reported_as_verified()
    test_word_home_selects_blank_template_before_pasting()
    test_stale_expected_clipboard_cannot_verify_empty_document()
    test_home_or_loading_titles_are_not_documents()
    test_word_com_creates_and_verifies_document()
    print("PASS: Word COM creates a document, inserts text, and verifies it")
