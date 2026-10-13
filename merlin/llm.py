"""Ollama API client optimizado basado en pruebas de parámetros."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import List, Dict, Optional

import httpx

from merlin.config import MerlinConfig, get_config

logger = logging.getLogger("merlin.llm")


class LLMConnector:
    """Talks to Ollama with optimized parameters."""

    def __init__(self, config: Optional[MerlinConfig] = None, 
                 model_name: Optional[str] = None, think: bool = True) -> None:
        self.config = config or get_config()
        self.model_name = model_name or self.config.model_name
        self.think = think
        self.available: Optional[bool] = None
        self._detected_model: Optional[str] = None
        
        # FIX: normaliza la URL - quita /v1 si viene del .env heredado de LM Studio
        base = self.config.lm_studio_base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        self._base = base

    def health_check(self) -> bool:
        """Verifica si Ollama está disponible y detecta el modelo configurado."""
        try:
            resp = httpx.get(f"{self._base}/api/tags", timeout=5)
            if resp.status_code != 200:
                logger.warning("Ollama respondió HTTP %d", resp.status_code)
                self.available = False
                return False
            
            data = resp.json()
            available_models = [m.get("name", "") for m in data.get("models", [])]
            
            # 1. Buscar el modelo exacto que pedimos en el config
            target = self.model_name
            for name in available_models:
                if target == name or target in name or name.startswith(target + ":"):
                    self._detected_model = name
                    self.available = True
                    logger.info("Ollama OK - serving: %s (configurado: %s)",
                            self._detected_model, target)
                    return True
            
            # 2. Si el perfil es "fast" y apunta al mismo modelo que "main",
            #    reutilizar el que ya está cargado
            for name in available_models:
                if name.startswith("gemma4:") or name.startswith("qwen3:"):
                    logger.warning("Modelo %s no encontrado; usando %s como fallback",
                                target, name)
                    self._detected_model = name
                    self.available = True
                    return True
            
            # 3. Ningún modelo disponible
            logger.warning("Modelo '%s' no está en Ollama. Disponibles: %s",
                        target, available_models)
            logger.info("Descárgalo con: ollama pull %s", target)
            self.available = False
            return False
            
        except Exception as e:
            logger.warning("Ollama unreachable: %s", e)
            self.available = False
            return False

    def chat(self, messages: List[Dict[str, str]], fallback: str = "",
             on_token=None) -> str:
        """Envía mensajes a Ollama con parámetros optimizados."""
        if self.available is None and not self.health_check():
            logger.warning("LLM offline - using fallback")
            return fallback

        payload = {
            "model": self._detected_model or self.model_name,
            "messages": messages,
            "stream": on_token is not None,
            "think": self.think,
            "keep_alive": "5m",
            "options": {
                "num_ctx": 8192
            }
        }

        try:
            if on_token:
                return self._stream_chat(payload, fallback, on_token)
            else:
                return self._sync_chat(payload, fallback)
        except Exception as e:
            logger.error("LLM error: %s", e)
            self.available = False
            return fallback

    def _sync_chat(self, payload: dict, fallback: str) -> str:
        start_time = time.time()
        
        response = httpx.post(
            f"{self._base}/api/chat",                                  # ← _base
            json=payload,
            timeout=self.config.llm_timeout_seconds
        )
        
        if response.status_code != 200:
            logger.error("LLM HTTP %d: %s", response.status_code, response.text[:200])
            return fallback
        
        elapsed = time.time() - start_time
        data = response.json()
        content = data.get("message", {}).get("content", "").strip()
        
        if not content:
            logger.warning("LLM returned empty content")
            return fallback
        
        load_ms = data.get("load_duration", 0) / 1_000_000
        eval_ms = data.get("eval_duration", 0) / 1_000_000
        tokens = data.get("eval_count", 0)
        speed = tokens / (eval_ms / 1000) if eval_ms > 0 else 0
        
        logger.info(f"LLM call: {elapsed:.1f}s, load: {load_ms:.0f}ms, "
                   f"gen: {eval_ms/1000:.1f}s, {speed:.1f} tok/s, "
                   f"tokens: {tokens}")
        
        self.available = True
        return content

    def _stream_chat(self, payload: dict, fallback: str, on_token) -> str:
        start_time = time.time()
        content_parts = []
        
        try:
            with httpx.stream(
                "POST",
                f"{self._base}/api/chat",                              # ← _base
                json=payload,
                timeout=self.config.llm_timeout_seconds
            ) as response:
                if response.status_code != 200:
                    logger.error("LLM stream HTTP %d", response.status_code)
                    return fallback
                
                for line in response.iter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            if "message" in data:
                                chunk = data["message"].get("content", "")
                                if chunk:
                                    content_parts.append(chunk)
                                    on_token("".join(content_parts))
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error("LLM stream error: %s", e)
            return fallback
        
        elapsed = time.time() - start_time
        full_content = "".join(content_parts).strip()
        
        if not full_content:
            logger.warning("LLM stream returned empty content")
            return fallback
        
        logger.info(f"LLM stream: {elapsed:.1f}s, {len(content_parts)} chunks, "
                   f"{len(full_content)} chars")
        
        self.available = True
        return full_content


_CONNECTORS: Dict[str, LLMConnector] = {}


def get_llm(profile: str = "main") -> LLMConnector:
    """Retorna una instancia del cliente Ollama (cacheada por perfil)."""
    if profile in _CONNECTORS:
        return _CONNECTORS[profile]
    
    config = get_config()
    
    if profile == "fast" and config.fast_model_name:
        connector = LLMConnector(config, model_name=config.fast_model_name, think=False)
    else:
        connector = LLMConnector(config, think=True)
    
    _CONNECTORS[profile] = connector
    return connector


# Alias para compatibilidad
OllamaClient = LLMConnector