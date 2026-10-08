"""Connector to LM Studio's OpenAI-compatible API with graceful offline fallback."""
from __future__ import annotations

import logging
import os
import time
from functools import lru_cache
from typing import List, Optional

import httpx

from merlin.config import MerlinConfig, get_config

logger = logging.getLogger("merlin.llm")


class LLMConnector:
    """Talks to LM Studio (OpenAI-compatible). Degrades gracefully when offline."""

    def __init__(self, config: Optional[MerlinConfig] = None) -> None:
        self.config = config or get_config()
        self._chat_model = None
        self._detected_model: Optional[str] = None
        self.available: Optional[bool] = None

    def _headers(self) -> dict:
        if self.config.lm_studio_api_key:
            return {"Authorization": f"Bearer {self.config.lm_studio_api_key}"}
        return {}

    def detect_model(self) -> Optional[str]:
        """Auto-detect the served model id via GET /v1/models."""
        url = self.config.lm_studio_base_url.rstrip("/") + "/models"
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                ids = [m.get("id", "") for m in data]
                for mid in ids:
                    if self.config.model_name.lower() in mid.lower():
                        self._detected_model = mid
                        return mid
                if ids:
                    self._detected_model = ids[0]
                    return ids[0]
            else:
                logger.warning("LM Studio returned HTTP %s (check API key in .env)",
                                resp.status_code)
        except Exception as exc:  # network down, timeout...
            logger.warning("LM Studio unreachable at %s: %s", url, exc)
        return None

    def health_check(self) -> bool:
        model = self.detect_model()
        self.available = model is not None
        if model:
            logger.info("LM Studio OK - serving model: %s", model)
        else:
            logger.warning("LM Studio NOT available - MERLIN runs in degraded mode")
        return self.available

    def _get_chat_model(self):
        if self._chat_model is None:
            from langchain_openai import ChatOpenAI  # deferred import

            kwargs = {}
            # Optional: LLM_REASONING_EFFORT=low|medium|high. Only models whose
            # chat template supports it will honour it; otherwise use the
            # "thinking" toggle in LM Studio's model settings.
            effort = os.environ.get("LLM_REASONING_EFFORT")
            if effort:
                kwargs["extra_body"] = {"reasoning_effort": effort}

            self._chat_model = ChatOpenAI(
                model=self._detected_model or self.config.model_name,
                temperature=self.config.temperature,
                base_url=self.config.lm_studio_base_url,
                api_key=self.config.lm_studio_api_key or "lm-studio",
                timeout=self.config.llm_timeout_seconds,
                max_tokens=self.config.llm_max_tokens,
                **kwargs,
            )
        return self._chat_model

    def chat(self, messages: List[dict], fallback: str = "") -> str:
        """Invoke the LLM. Returns `fallback` (and logs) when the server is down."""
        if self._detected_model is None and not self.health_check():
            logger.warning("LLM offline - using fallback response")
            return fallback
        try:
            started = time.perf_counter()
            response = self._get_chat_model().invoke(messages)
            elapsed = time.perf_counter() - started
            self.available = True

            usage = getattr(response, "usage_metadata", None) or {}
            out_tokens = usage.get("output_tokens", 0)
            logger.info("LLM call: %.1fs, %d output tokens (%.1f tok/s)",
                        elapsed, out_tokens,
                        out_tokens / elapsed if elapsed else 0.0)

            content = (getattr(response, "content", "") or "").strip()
            if content:
                return content
            # Thinking models can exhaust the token budget on reasoning;
            # an empty content is a failure for us -> degrade gracefully.
            logger.warning("LLM returned empty content (budget spent on reasoning? "
                           "max_tokens=%s)", self.config.llm_max_tokens)
            self.available = False
            return fallback
        except Exception as exc:
            self.available = False
            logger.warning("LLM invocation failed (%s) - using fallback response", exc)
            return fallback


@lru_cache(maxsize=1)
def get_llm() -> LLMConnector:
    return LLMConnector()