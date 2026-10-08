"""Local JSON persistence: execution logs + chat history (Qdrant/Postgres-ready)."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Union

from merlin.config import MerlinConfig, ensure_dirs, get_config

logger = logging.getLogger("merlin.persistence")

_MAX_ENTRIES = 500  # keep JSON stores bounded on disk


def _read_json(path: Path, default: Union[dict, list]) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Corrupted store %s (%s) - starting fresh", path, exc)
    return default


def _write_json(path: Path, data: Any) -> None:
    ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def execution_log_path(config: MerlinConfig) -> Path:
    return config.logs_dir / "execution_logs.json"


def chat_history_path(config: MerlinConfig) -> Path:
    return config.logs_dir / "chat_history.json"


def load_execution_logs(config: MerlinConfig = None) -> List[dict]:
    config = config or get_config()
    return _read_json(execution_log_path(config), [])


def append_execution_log(entry: Dict[str, Any]) -> None:
    config = get_config()
    logs = load_execution_logs(config)
    entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    logs.append(entry)
    _write_json(execution_log_path(config), logs[-_MAX_ENTRIES:])


def load_chat_history(config: MerlinConfig = None) -> List[dict]:
    config = config or get_config()
    return _read_json(chat_history_path(config), [])


def append_chat_message(role: str, content: str) -> None:
    config = get_config()
    history = load_chat_history(config)
    history.append({
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    _write_json(chat_history_path(config), history[-_MAX_ENTRIES:])


def load_recent_chat(n: int, config: MerlinConfig = None) -> List[dict]:
    return load_chat_history(config)[-n:]


def now_ms() -> int:
    return int(time.time() * 1000)
