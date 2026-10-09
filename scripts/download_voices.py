"""Downloads the Piper TTS voices MERLIN needs into ~/merlin/voices/.

Voices (per config.piper_voices):
  - es_ES-sharvard-medium  (Spanish)
  - en_GB-alan-medium      (British English)

Run:  python scripts/download_voices.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merlin.config import get_config  # noqa: E402

_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
_VOCAB = {"es_ES": "es/es_ES/sharvard/medium/es_ES-sharvard-medium",
          "en_GB": "en/en_GB/alan/medium/en_GB-alan-medium"}


def voice_dir() -> Path:
    d = Path.home() / "merlin" / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download(voice_name: str) -> Path | None:
    """Download <voice>.onnx + <voice>.onnx.json if missing. Returns model path."""
    prefix = None
    for _lang, vocab in _VOCAB.items():
        if voice_name.startswith(_lang):
            prefix = vocab
    if prefix is None:  # future custom voices: infer from the name
        parts = voice_name.split("-")
        prefix = f"{parts[0][:2]}/{parts[0]}/{parts[1]}"
    target = voice_dir() / f"{voice_name}.onnx"
    if target.exists() and target.with_suffix(".onnx.json").exists():
        print(f"  {voice_name}: already downloaded")
        return target
    for suffix in (".onnx", ".onnx.json"):
        url = _BASE + prefix + suffix
        dest = voice_dir() / f"{voice_name}{suffix}"
        print(f"  downloading {url} ...")
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(65536):
                    f.write(chunk)
    print(f"  {voice_name}: OK ({target.stat().st_size / 1e6:.1f} MB)")
    return target


def main() -> None:
    config = get_config()
    print(f"Voice directory: {voice_dir()}")
    for language, voice_name in config.piper_voices.items():
        download(voice_name)
    print("All voices ready.")


if __name__ == "__main__":
    main()
