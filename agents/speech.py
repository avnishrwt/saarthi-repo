"""
Voice note -> text (PRD FR14). Pluggable, because brief open decision #3 (Whisper vs
Bhashini) is not decided yet.

Engines:
- "faster_whisper": local Whisper (pip install faster-whisper). Model via RAHAT_WHISPER_MODEL.
- "none" (default): returns None, and the message goes to the human review queue
  marked "audio received, no speech-to-text engine". It is never dropped.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

log = logging.getLogger("rahat.speech")
ENGINE = os.getenv("RAHAT_STT", "none")


@lru_cache(maxsize=1)
def _whisper():
    from faster_whisper import WhisperModel
    return WhisperModel(os.getenv("RAHAT_WHISPER_MODEL", "small"), device="cpu", compute_type="int8")


def transcribe(path: str) -> tuple[Optional[str], str]:
    """Return (text or None, engine name)."""
    if ENGINE == "faster_whisper":
        try:
            segments, info = _whisper().transcribe(path, language=None, vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
            return (text or None), f"faster_whisper({info.language})"
        except Exception as e:
            log.warning("transcription failed: %s", e)
            return None, "faster_whisper(failed)"
    return None, "none"
