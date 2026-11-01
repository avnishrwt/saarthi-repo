"""
Voice note -> text (PRD FR14). The text then goes through the SAME extractor agent as typed messages.

Engines (RAHAT_STT):
- "gemini" (default when GEMINI_API_KEY is set): the Gemini model reads the audio directly.
  Hinglish / Hindi / English; we ask for the words exactly as spoken, nothing added.
- "faster_whisper": local Whisper (pip install faster-whisper). Model via RAHAT_WHISPER_MODEL.
  If Gemini fails (quota, outage) and faster-whisper is installed, it is tried as a fallback.
- "none": no engine. The message goes to the human review queue marked
  "audio received, no speech-to-text engine". A voice note is never dropped.

Formats: WhatsApp sends OGG/Opus, browsers record WebM/Opus. Formats Gemini may not accept are
converted to 16 kHz mono WAV with ffmpeg when ffmpeg is installed.
Failure of any kind -> (None, reason): the caller sends the note to human review.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Optional

from shared import config

log = logging.getLogger("rahat.speech")


def engine() -> str:
    e = os.getenv("RAHAT_STT")
    if e:
        return e
    return "gemini" if config.LLM_PROVIDER == "gemini" else "none"


MIME = {".wav": "audio/wav", ".mp3": "audio/mp3", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
        ".flac": "audio/flac", ".aac": "audio/aac", ".m4a": "audio/aac", ".aiff": "audio/aiff"}
MAX_BYTES = 15 * 1024 * 1024        # inline request limit is ~20 MB; a voice note is far smaller

PROMPT = ("Transcribe this voice message from a person in a flood emergency in India. Write exactly the words "
          "spoken, in the language spoken (Hindi in Devanagari, Hinglish in Roman letters, English as is). "
          "Do not translate, summarise, correct or add anything. Keep place names, numbers and ages exactly. "
          "If you cannot hear any speech, reply with exactly: NO_SPEECH")


def _to_wav(path: str) -> Optional[str]:
    """WebM / AMR / unknown -> 16 kHz mono WAV via ffmpeg (None if ffmpeg is missing or fails)."""
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    out = os.path.join(tempfile.mkdtemp(prefix="rahat-stt-"), "note.wav")
    try:
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", "16000", out],
                       check=True, timeout=60, capture_output=True)
        return out
    except Exception as e:
        log.warning("ffmpeg conversion failed: %s", e)
        return None


def _gemini(path: str) -> tuple[Optional[str], str]:
    from agents import llm                      # shares the client, rate limit and circuit breaker
    if not llm.available():
        return None, "gemini(paused: provider errors)"
    p = Path(path)
    mime = MIME.get(p.suffix.lower())
    if mime is None:                           # e.g. .webm from the browser, .amr from old phones
        wav = _to_wav(path)
        if wav is None:
            return None, f"gemini(cannot read {p.suffix or 'this'} audio: install ffmpeg)"
        p, mime = Path(wav), "audio/wav"
    data = p.read_bytes()
    if len(data) > MAX_BYTES:
        return None, "gemini(audio too long)"
    from google.genai import types
    try:
        with llm._slots:
            resp = llm._client("gemini").models.generate_content(
                model=config.LLM_MODEL,
                contents=[types.Part.from_bytes(data=data, mime_type=mime), PROMPT])
        llm._record(True)
    except Exception as e:
        llm._record(False)
        log.warning("gemini transcription failed: %s", str(e)[:200])
        return None, "gemini(failed)"
    text = (resp.text or "").strip()
    if not text or text.upper().startswith("NO_SPEECH"):
        return None, "gemini(no speech heard)"
    return text, f"gemini:{config.LLM_MODEL}"


@lru_cache(maxsize=1)
def _whisper():
    from faster_whisper import WhisperModel
    return WhisperModel(os.getenv("RAHAT_WHISPER_MODEL", "small"), device="cpu", compute_type="int8")


def transcribe(path: str) -> tuple[Optional[str], str]:
    """Return (text or None, engine name / reason)."""
    if not path or not os.path.exists(path):
        return None, "no audio file"
    # only files we saved ourselves (uploads/): audio_url can come from a public request, and we must
    # never read and send any other file on this machine to an outside service
    uploads = (config.ROOT / "uploads").resolve()
    if uploads not in Path(path).resolve().parents:
        log.warning("refused to transcribe a file outside uploads/: %s", path)
        return None, "audio file not in uploads/ (refused)"
    e = engine()
    if e == "gemini":
        text, why = _gemini(path)
        if text is None and "no speech heard" not in why and whisper_installed():
            text, why2 = _whisper_text(path)          # quota / outage: try the local engine
            return text, f"{why}, then {why2}"
        return text, why
    if e == "faster_whisper":
        return _whisper_text(path)
    return None, "none"


def whisper_installed() -> bool:
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None


def _pcm16k(path: str):
    """Decode any audio to 16 kHz mono float32 with ffmpeg. faster-whisper's own decoder (PyAV)
    breaks on newer PyAV versions, so we hand it samples instead. None if ffmpeg is missing."""
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    import numpy as np
    out = subprocess.run([ff, "-nostdin", "-loglevel", "error", "-i", path, "-f", "f32le", "-ac", "1",
                          "-ar", "16000", "-"], check=True, timeout=60, capture_output=True).stdout
    return np.frombuffer(out, dtype=np.float32)


def _whisper_text(path: str) -> tuple[Optional[str], str]:
    try:
        audio = _pcm16k(path)
        src = path if audio is None else audio
        # Auto-detect hears Hindi as Urdu (Urdu script), which the extractor cannot read, so the
        # language is fixed (default Hindi). Whisper's Hindi spellings are irregular and an English
        # translation drops place names, so we keep BOTH: words as spoken + English in brackets.
        lang = os.getenv("RAHAT_WHISPER_LANG", "hi") or None
        m = _whisper()
        segments, info = m.transcribe(src, language=lang, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
        if text and info.language != "en":
            segments, _ = m.transcribe(src, language=info.language, task="translate", vad_filter=True)
            en = " ".join(s.text.strip() for s in segments).strip()
            if en:
                text = f"{text} [English: {en}]"
        return (text or None), f"faster_whisper({info.language})"
    except Exception as ex:
        log.warning("transcription failed: %s", ex)
        return None, "faster_whisper(failed)"
