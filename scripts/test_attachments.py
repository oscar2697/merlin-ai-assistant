"""Offline checks for attachment resolution and social-action safeguards."""
from __future__ import annotations

import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.agents import os_agent
from merlin import confirm
from merlin.tools import attachments, linkedin_tools


def main() -> None:
    with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent) as folder:
        root = Path(folder)
        text_file = root / "notes.txt"
        image_file = root / "capture.PNG"
        text_file.write_text("attachment", encoding="utf-8")
        image_file.write_bytes(b"image")

        assert attachments.resolve_file(str(text_file)) == text_file.resolve()
        assert attachments.resolve_file(str(root / "missing.png")) is None

        with patch.object(attachments, "SCREENSHOTS_DIR", root):
            (root / "merlin_20261019_120000.png").write_bytes(b"old")
            (root / "merlin_20261019_120001.png").write_bytes(b"new")
            assert attachments.resolve_file("last").name.endswith("120001.png")
            assert attachments.resolve_file("ultima").name.endswith("120001.png")

        assert linkedin_tools._resolve_image(str(image_file)) == str(image_file.resolve())
        assert linkedin_tools._resolve_image(str(text_file)) is None

    with patch.dict("os.environ", {"MERLIN_WHATSAPP_METHOD": "deeplink",
                                    "MERLIN_CONFIRM_SEND": "0"}), \
            patch("merlin.agents.os_agent.whatsapp_tools.resolve_contact",
                  return_value=None):
        confirm.clear_pending()
        prompt = os_agent._ask_action_confirmation(
            {"tool": "send_whatsapp_media", "contact": "unknown",
             "file_path": "report.pdf"}, "en")
        assert prompt and "report.pdf" in prompt
        assert confirm.peek_pending()["plan"]["file_path"] == "report.pdf"
        confirm.clear_pending()

    wa_status = os_agent._social_plan("Sube esta foto a mi estado de WhatsApp")
    assert wa_status == {
        "tool": "publish_social", "platform": "whatsapp", "mode": "story",
        "text": "", "file_path": "last"}
    facebook_story = os_agent._social_plan(
        "Publica una historia con esta foto en Facebook")
    assert facebook_story["platform"] == "facebook"
    assert facebook_story["mode"] == "story"
    assert facebook_story["file_path"] == "last"
    facebook_post = os_agent._social_plan(
        "Haz una publicación profesional sobre MERLIN en Facebook")
    assert facebook_post["mode"] == "post"
    assert "MERLIN" in facebook_post["text"]
    assert os_agent._social_plan("¿Qué es una historia de Facebook?") is None
    explicit_file = os_agent._social_plan(
        'Publica "C:\\Users\\lindo\\Pictures\\sample.png" en Facebook')
    assert explicit_file["file_path"] == "C:\\Users\\lindo\\Pictures\\sample.png"
    assert os_agent._social_plan("Publica una actualización en LinkedIn") is None

    stage_prompt = os_agent._ask_staged_confirmation(
        {"tool": "publish_social", "platform": "facebook", "mode": "story",
         "text": "A project update"},
        {"stage_id": "stage-token", "text": "A project update",
         "file": "C:/capture.png"}, "en")
    assert stage_prompt and "Publish it?" in stage_prompt
    assert confirm.peek_pending()["plan"]["stage_id"] == "stage-token"
    confirm.clear_pending()

    rejected = os_agent._run_step({
        "tool": "publish_social", "platform": "facebook", "mode": "story",
        "publish": True, "stage_id": "missing-token"})[2]
    assert not rejected["ok"]
    assert "staged post expired" in rejected["error"]

    overlay_source = (Path(__file__).resolve().parent.parent /
                      "merlin" / "gui" / "cursor_overlay.py").read_text(
                          encoding="utf-8")
    panel_source = overlay_source.split("def _draw_info_panel", 1)[1].split(
        "def _drain_pings", 1)[0]
    assert 'text="RESULTS"' in panel_source
    assert 'text="Answer and research"' in panel_source
    assert "RESULTADOS" not in panel_source
    assert "Respuesta e investigación" not in panel_source

    response = os_agent.os_agent_node({
        "input_query": "Sube esta foto a mi historia de Instagram",
        "detected_language": "es",
        "tool_calls": [],
        "tool_results": [],
    })
    assert "No realicé ninguna acción" in response["final_response"]
    assert "tool_calls" not in response

    print("PASS: attachments, social routing, staged confirmation, and safe publish guards")


if __name__ == "__main__":
    main()
