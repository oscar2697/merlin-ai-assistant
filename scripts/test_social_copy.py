"""Social copy uses its platform tone without describing unseen images."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.tools import linkedin_tools, social_tools


def test_story_caption_is_casual_and_does_not_claim_vision():
    llm = SimpleNamespace(chat=Mock(return_value="Un momento para compartir ✨"))
    with patch("merlin.llm.get_llm", return_value=llm):
        caption = social_tools._compose_caption("whatsapp", "story", "", "es")

    prompt = llm.chat.call_args.args[0]
    assert caption == "Un momento para compartir ✨"
    assert "casual" in prompt[0]["content"]
    assert "cannot see the image" in prompt[0]["content"]
    assert "not been analyzed" in prompt[1]["content"]


def test_linkedin_copy_is_professional_and_grounded_in_request():
    llm = SimpleNamespace(chat=Mock(return_value="Comparto un avance profesional."))
    with patch("merlin.llm.get_llm", return_value=llm):
        draft = linkedin_tools._compose_draft("mi proyecto", "es")

    prompt = llm.chat.call_args.args[0]
    assert draft == "Comparto un avance profesional."
    assert "professional and credible" in prompt[0]["content"]
    assert "Do not claim to have inspected an attached image" in prompt[0]["content"]
    assert "Requested language: Spanish" in prompt[1]["content"]


def test_image_post_without_caption_gets_platform_copy_before_staging():
    with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent) as folder:
        image = Path(folder) / "capture.png"
        image.write_bytes(b"image")
        staged = {}

        def stage(_platform, _mode, text, _file, _token):
            staged["text"] = text
            return {"ok": True, "staged": True, "text": text}

        with patch.object(social_tools, "resolve_file", return_value=image), \
                patch.object(social_tools, "_compose_caption",
                             return_value="Un momento para compartir ✨"), \
                patch.object(social_tools, "_on_browser_thread",
                             side_effect=lambda operation: operation()), \
                patch.object(social_tools, "_stage", side_effect=stage):
            result = social_tools.publish_social(
                "whatsapp", "story", file_path="last", language="es")

    assert result["ok"]
    assert staged["text"] == "Un momento para compartir ✨"


if __name__ == "__main__":
    test_story_caption_is_casual_and_does_not_claim_vision()
    test_linkedin_copy_is_professional_and_grounded_in_request()
    test_image_post_without_caption_gets_platform_copy_before_staging()
    print("PASS: LinkedIn and story copy follow their platform styles")
