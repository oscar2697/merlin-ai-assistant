"""The configured Ollama generation limits must reach its API payload."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.config import MerlinConfig
from merlin.llm import LLMConnector


def test_generation_config_is_sent_to_ollama():
    config = MerlinConfig(temperature=0.37, llm_max_tokens=321)
    connector = LLMConnector(config)
    connector.available = True
    captured = {}

    def sync_chat(payload, _fallback):
        captured.update(payload)
        return "ok"

    with patch.object(connector, "_sync_chat", side_effect=sync_chat):
        assert connector.chat([{"role": "user", "content": "test"}]) == "ok"

    assert captured["options"]["temperature"] == 0.37
    assert captured["options"]["num_predict"] == 321


if __name__ == "__main__":
    test_generation_config_is_sent_to_ollama()
    print("PASS: configured temperature and token limit reach Ollama")
