"""Offline checks that attachment helpers verify the selected image."""
from __future__ import annotations

import sys
import ast
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.tools import linkedin_tools, social_tools


class _Input:
    def __init__(self, accept_file: bool = True):
        self.filename = ""
        self.accept_file = accept_file

    def set_input_files(self, path, timeout=0):
        if self.accept_file:
            self.filename = Path(path).name

    def evaluate(self, _script, expected):
        return self.filename.casefold() == expected.casefold()


class _Collection:
    def __init__(self, items):
        self.items = items
        self.first = items[0] if items else _Input()

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class _Page:
    def __init__(self, input_element):
        self.input_element = input_element

    def locator(self, _selector):
        return _Collection([self.input_element])

    def get_by_role(self, *_args, **_kwargs):
        return _Collection([])

    def get_by_text(self, *_args, **_kwargs):
        return _Collection([])

    def wait_for_timeout(self, _duration):
        pass


def test_linkedin_rejects_unconfirmed_file_selection():
    accepted = _Page(_Input(accept_file=True))
    rejected = _Page(_Input(accept_file=False))
    assert linkedin_tools._attach_image(accepted, "C:/images/capture.png")
    assert not linkedin_tools._attach_image(rejected, "C:/images/capture.png")
    assert linkedin_tools._image_is_attached(accepted, "capture.png")
    assert not linkedin_tools._image_is_attached(rejected, "capture.png")


def test_linkedin_publish_requires_matching_review_token():
    assert not linkedin_tools._publish_impl("stale-token")["ok"]


def test_social_upload_checks_filename_and_survives_portaled_inputs():
    accepted = _Page(_Input(accept_file=True))
    rejected = _Page(_Input(accept_file=False))
    assert social_tools._attach_image(accepted, "C:/images/capture.png")
    assert not social_tools._attach_image(rejected, "C:/images/capture.png")
    assert social_tools._file_is_still_attached(
        accepted, "capture.png")


class _Button:
    def __init__(self, name, visible=True):
        self.name = name
        self.visible = visible
        self.clicked = False

    def is_visible(self):
        return self.visible

    def click(self, timeout=0):
        self.clicked = True


class _ButtonCollection:
    def __init__(self, buttons):
        self.buttons = buttons
        self.first = buttons[0] if buttons else _Button("", visible=False)

    def count(self):
        return len(self.buttons)

    def nth(self, index):
        return self.buttons[index]


class _StatusComposer:
    def __init__(self):
        self.audience_button = _Button("Status (Contacts)")
        self.send_button = _Button("Send")

    def get_by_role(self, role, name=None):
        assert role == "button"
        return _ButtonCollection([button for button in
                                  (self.audience_button, self.send_button)
                                  if name.search(button.name)])

    def locator(self, _selector):
        return _ButtonCollection([_Button("", visible=False)])


def test_whatsapp_icon_send_button_can_be_clicked_by_accessible_name():
    composer = _StatusComposer()
    assert social_tools._click_whatsapp_publish(composer)
    assert composer.send_button.clicked
    assert not composer.audience_button.clicked


def test_whatsapp_audience_control_is_not_mistaken_for_publish():
    composer = _StatusComposer()
    composer.send_button.visible = False
    assert not social_tools._click_whatsapp_publish(composer)
    assert not social_tools._whatsapp_publish_control_visible(composer)


def test_cursor_public_api_functions_have_single_definitions():
    source = (Path(__file__).resolve().parent.parent /
              "merlin" / "gui" / "cursor_overlay.py").read_text(
                  encoding="utf-8")
    tree = ast.parse(source)
    names = {"announce", "announce_click", "announce_wait",
             "current_position", "agent_position"}
    counts = {name: 0 for name in names}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in counts:
                counts[node.name] += 1
    assert all(count == 1 for count in counts.values()), counts


if __name__ == "__main__":
    test_linkedin_rejects_unconfirmed_file_selection()
    test_linkedin_publish_requires_matching_review_token()
    test_social_upload_checks_filename_and_survives_portaled_inputs()
    test_whatsapp_icon_send_button_can_be_clicked_by_accessible_name()
    test_whatsapp_audience_control_is_not_mistaken_for_publish()
    test_cursor_public_api_functions_have_single_definitions()
    print("PASS: social attachment helpers require the requested filename")
