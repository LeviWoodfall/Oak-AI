"""
Whisper Speech-to-Text — voice note-taking powered by OpenAI Whisper.
Uses the faster-whisper library (CTranslate2 backend) for local inference.
Falls back to the openai-whisper package if faster-whisper is unavailable.

Audio is received as base64 or file upload, transcribed locally, and
optionally saved to Joplin as a note.
"""
import asyncio
import base64
import logging
import tempfile
import time
from pathlib import Path
from typing import Optional
from backend.config import DATA_DIR

logger = logging.getLogger("oak.whisper")

AUDIO_DIR = DATA_DIR / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Whisper model sizes: tiny (39M), base (74M), small (244M), medium (769M), large (1550M)
DEFAULT_MODEL = "base"


class WhisperService:
    """Local speech-to-text using Whisper."""

    def __init__(self):
        self._model = None
        self._model_name = DEFAULT_MODEL
        self._backend = None  # "faster-whisper" or "openai-whisper"

    @property
    def available(self) -> bool:
        return self._model is not None

    def load_model(self, model_name: str = None):
        """Load the Whisper model. Call once at startup or on demand."""
        model_name = model_name or self._model_name

        # Try faster-whisper first (much faster, lower RAM)
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(model_name, device="cpu", compute_type="int8")
            self._backend = "faster-whisper"
            self._model_name = model_name
            logger.info("Loaded faster-whisper model: %s", model_name)
            return
        except ImportError:
            pass

        # Fall back to openai-whisper
        try:
            import whisper
            self._model = whisper.load_model(model_name)
            self._backend = "openai-whisper"
            self._model_name = model_name
            logger.info("Loaded openai-whisper model: %s", model_name)
            return
        except ImportError:
            pass

        logger.warning("No Whisper library available. Install: pip install faster-whisper")

    async def transcribe_file(self, audio_path: str, language: str = None) -> dict:
        """Transcribe an audio file to text."""
        if not self._model:
            self.load_model()
        if not self._model:
            return {"error": "Whisper not available. Install: pip install faster-whisper", "text": ""}

        start = time.time()
        path = Path(audio_path)
        if not path.exists():
            return {"error": f"File not found: {audio_path}", "text": ""}

        try:
            if self._backend == "faster-whisper":
                text, segments_data = await self._transcribe_faster(str(path), language)
            else:
                text, segments_data = await self._transcribe_openai(str(path), language)

            elapsed = round(time.time() - start, 2)
            return {
                "text": text,
                "segments": segments_data,
                "duration_seconds": elapsed,
                "model": self._model_name,
                "backend": self._backend,
            }
        except Exception as e:
            logger.error("Transcription failed: %s", e)
            return {"error": str(e), "text": ""}

    async def transcribe_base64(self, audio_b64: str, filename: str = "recording.webm",
                                 language: str = None) -> dict:
        """Transcribe base64-encoded audio data."""
        # Decode and save to temp file
        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception as e:
            return {"error": f"Invalid base64 audio: {e}", "text": ""}

        suffix = Path(filename).suffix or ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, dir=str(AUDIO_DIR), delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            result = await self.transcribe_file(tmp_path, language)
            return result
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass

    async def transcribe_and_save_note(self, audio_b64: str, title: str = "",
                                        language: str = None, tags: list[str] = None) -> dict:
        """Transcribe audio and save directly to Joplin as a note."""
        result = await self.transcribe_base64(audio_b64, language=language)
        if result.get("error") or not result.get("text"):
            return result

        text = result["text"]
        if not title:
            # Auto-title from first 60 chars
            title = f"Voice Note: {text[:60]}{'...' if len(text) > 60 else ''}"

        # Save to Joplin
        try:
            from backend.joplin_service import joplin_service
            if joplin_service.configured:
                note = await joplin_service.save_ai_note(
                    title=title,
                    content=f"*Transcribed via Whisper ({self._model_name})*\n\n{text}",
                    tags=(tags or []) + ["voice-note", "whisper"],
                )
                result["joplin_note_id"] = note.get("id", "")
                result["joplin_saved"] = True
            else:
                result["joplin_saved"] = False
        except Exception as e:
            result["joplin_saved"] = False
            result["joplin_error"] = str(e)

        return result

    # ── Backend-specific transcription ───────────────────────────────

    async def _transcribe_faster(self, path: str, language: str = None) -> tuple[str, list]:
        """Transcribe using faster-whisper (runs in thread pool)."""
        def _run():
            kwargs = {}
            if language:
                kwargs["language"] = language
            segments, info = self._model.transcribe(path, **kwargs)
            seg_list = []
            full_text = ""
            for seg in segments:
                full_text += seg.text
                seg_list.append({
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text.strip(),
                })
            return full_text.strip(), seg_list

        return await asyncio.get_event_loop().run_in_executor(None, _run)

    async def _transcribe_openai(self, path: str, language: str = None) -> tuple[str, list]:
        """Transcribe using openai-whisper (runs in thread pool)."""
        def _run():
            kwargs = {"fp16": False}
            if language:
                kwargs["language"] = language
            result = self._model.transcribe(path, **kwargs)
            segments = [
                {"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()}
                for s in result.get("segments", [])
            ]
            return result["text"].strip(), segments

        return await asyncio.get_event_loop().run_in_executor(None, _run)

    def status(self) -> dict:
        return {
            "available": self.available,
            "model": self._model_name,
            "backend": self._backend,
        }


whisper_service = WhisperService()
