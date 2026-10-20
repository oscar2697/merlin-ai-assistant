"""The floating results card receives the same incremental answer as the UI."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.gui.app import MerlinApp


def test_partial_response_is_sent_to_results_card_immediately():
    app = SimpleNamespace(chat=object(), _stream_bubble=None)
    bubble = Mock()
    with patch("merlin.gui.app._Bubble", return_value=bubble), \
            patch("merlin.gui.app.cursor_overlay.task_result") as show_result:
        MerlinApp._render_partial(app, "La relatividad")
        MerlinApp._render_partial(app, "La relatividad general")

    assert show_result.call_args_list[0].args == ("La relatividad",)
    assert show_result.call_args_list[1].args == ("La relatividad general",)
    bubble.set_text.assert_called_once_with("La relatividad general")


if __name__ == "__main__":
    test_partial_response_is_sent_to_results_card_immediately()
    print("PASS: the results card updates as answer text streams")
