"""Social plans strip request boilerplate without mangling real captions.

Covers the two commands from the voice logs: the anaphoric LinkedIn upload and
the WhatsApp status that used to receive the request itself as its caption.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.agents import os_agent


def test_whatsapp_status_request_has_no_garbage_caption():
    plan = os_agent._social_plan(
        "Puede subir la última captura de pantalla a una historia en WhatsApp.")
    assert plan == {
        "tool": "publish_social", "platform": "whatsapp", "mode": "story",
        "text": "", "file_path": "last",
    }


def test_whatsapp_followup_acknowledgment_is_not_used_as_caption():
    plan = os_agent._social_plan(
        "Está bien. Puedes subirla a una historia en WhatsApp.")
    assert plan is not None
    assert plan["text"] == ""
    assert plan["file_path"] == "last"


def test_linkedin_anaphora_attaches_the_last_screenshot():
    plan = os_agent._linkedin_plan(
        "Gracias, ahora la puedes subir a LinkedIn en una publicación.")
    assert plan["image_path"] == "last"
    assert plan["instruction"].startswith("Comparte una actualización profesional")


def test_linkedin_keeps_the_topic_and_skips_the_image():
    plan = os_agent._linkedin_plan("publica en LinkedIn sobre mi nuevo proyecto")
    assert plan["instruction"] == "mi nuevo proyecto"
    assert "image_path" not in plan


def test_facebook_caption_after_a_colon_is_verbatim():
    plan = os_agent._social_plan(
        "publica en Facebook: hoy empiezo un nuevo proyecto")
    assert plan["text"] == "hoy empiezo un nuevo proyecto"


def test_facebook_caption_keeps_words_after_the_request():
    plan = os_agent._social_plan(
        "haz un post en Facebook sobre mi nuevo proyecto")
    assert plan["text"] == "mi nuevo proyecto"
    plan = os_agent._social_plan("post on Facebook about my new project")
    assert plan["text"] == "my new project"


def test_story_with_only_an_image_has_no_caption():
    plan = os_agent._social_plan(
        "publica una historia en Facebook con la última captura")
    assert plan["text"] == ""
    assert plan["file_path"] == "last"


def test_caption_with_an_image_word_inside_is_not_truncated():
    plan = os_agent._social_plan(
        "publica en Facebook: mi nueva foto del equipo en la oficina")
    assert plan["text"] == "mi nueva foto del equipo en la oficina"


def test_polite_request_forms_are_recognized():
    for phrase in (
        "Puedes subir esta imagen a una historia en WhatsApp",
        "¿Puede subir la captura a mi estado de WhatsApp?",
        "Sube esta foto a mi estado de WhatsApp",
    ):
        plan = os_agent._social_plan(phrase)
        assert plan is not None, phrase
        assert plan["text"] == "", phrase
        assert plan["file_path"] == "last", phrase


def test_followup_capture_reference_uses_the_last_screenshot():
    plan = os_agent._linkedin_plan(
        "Así es lo que va a capturar publica en LinkedIn.")
    assert plan["image_path"] == "last"
    assert plan["instruction"].startswith("Comparte una actualización profesional")


def test_compound_capture_and_publish_becomes_two_steps():
    steps = os_agent._fast_steps(
        "Tamos una captura de pantalla y publica la en LinkedIn con un post "
        "a un contexto profesional.")
    assert steps and [step["tool"] for step in steps] == [
        "take_screenshot", "post_linkedin"], steps
    assert steps[1]["image_path"] == "last"
    assert "contexto" in steps[1]["instruction"]


if __name__ == "__main__":
    test_whatsapp_status_request_has_no_garbage_caption()
    test_whatsapp_followup_acknowledgment_is_not_used_as_caption()
    test_linkedin_anaphora_attaches_the_last_screenshot()
    test_linkedin_keeps_the_topic_and_skips_the_image()
    test_facebook_caption_after_a_colon_is_verbatim()
    test_facebook_caption_keeps_words_after_the_request()
    test_story_with_only_an_image_has_no_caption()
    test_caption_with_an_image_word_inside_is_not_truncated()
    test_polite_request_forms_are_recognized()
    test_followup_capture_reference_uses_the_last_screenshot()
    test_compound_capture_and_publish_becomes_two_steps()
    print("PASS: social intents keep captions and attach anaphoric screenshots")
