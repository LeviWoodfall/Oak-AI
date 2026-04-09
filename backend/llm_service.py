"""
LLM service — manages Ollama models and provides chat/completion endpoints.
Supports streaming via async generators.
"""
import json
import logging
import asyncio
from typing import AsyncGenerator, Optional
import httpx
from backend.config import settings, HARDWARE

logger = logging.getLogger("oak.llm")


class LLMService:
    """Thin wrapper around Ollama HTTP API with streaming support."""

    def __init__(self):
        self.base_url = settings.ollama_host
        self.model = settings.default_model
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=300)

    # ── Health ────────────────────────────────────────────────────────

    async def health_check(self) -> dict:
        """Check if Ollama is running and the model is available."""
        try:
            resp = await self._client.get("/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            model_ready = any(self.model in m for m in models)
            return {
                "ollama_running": True,
                "models_available": models,
                "active_model": self.model,
                "model_ready": model_ready,
                "hardware": HARDWARE.to_dict(),
            }
        except Exception as e:
            return {
                "ollama_running": False,
                "error": str(e),
                "active_model": self.model,
                "hardware": HARDWARE.to_dict(),
            }

    # ── Model management ─────────────────────────────────────────────

    async def pull_model(self, model_name: Optional[str] = None) -> AsyncGenerator[str, None]:
        """Pull a model from Ollama registry, streaming progress."""
        model = model_name or self.model
        async with self._client.stream(
            "POST", "/api/pull", json={"name": model}, timeout=None
        ) as resp:
            async for line in resp.aiter_lines():
                if line.strip():
                    yield line

    async def list_models(self) -> list[dict]:
        """List locally installed models."""
        try:
            resp = await self._client.get("/api/tags")
            resp.raise_for_status()
            return resp.json().get("models", [])
        except Exception:
            return []

    async def switch_model(self, model_name: str) -> dict:
        """Switch the active model."""
        self.model = model_name
        return {"active_model": self.model}

    # ── Chat ─────────────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        context_docs: Optional[list[str]] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat completion. Yields JSON chunks with partial content."""

        full_messages = []

        # Build system prompt with optional RAG context
        sys_content = system_prompt or self._default_system_prompt()
        if context_docs:
            sys_content += "\n\n--- Relevant Knowledge ---\n"
            for i, doc in enumerate(context_docs, 1):
                sys_content += f"\n[{i}] {doc}\n"
            sys_content += "\n--- End Knowledge ---\n"

        full_messages.append({"role": "system", "content": sys_content})
        full_messages.extend(messages)

        payload = {
            "model": self.model,
            "messages": full_messages,
            "stream": True,
            "options": {"temperature": temperature},
        }

        try:
            async with self._client.stream(
                "POST", "/api/chat", json=payload, timeout=None
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.strip():
                        yield line
        except httpx.ConnectError:
            yield json.dumps({
                "error": "Cannot connect to Ollama. Is it running? Start with: ollama serve"
            })

    async def generate(self, prompt: str, temperature: float = 0.3) -> AsyncGenerator[str, None]:
        """Raw completion (non-chat) for code generation tasks."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": True,
            "options": {"temperature": temperature},
        }
        try:
            async with self._client.stream(
                "POST", "/api/generate", json=payload, timeout=None
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.strip():
                        yield line
        except httpx.ConnectError:
            yield json.dumps({
                "error": "Cannot connect to Ollama. Is it running? Start with: ollama serve"
            })

    # ── Helpers ───────────────────────────────────────────────────────

    def _default_system_prompt(self) -> str:
        return (
            "You are Oak, a local AI coding assistant specialised in Python. "
            "You write clean, well-documented, production-ready code. "
            "When asked about code, provide complete working examples. "
            "Use markdown formatting with code blocks. "
            "If the user shares context from their wiki or repositories, "
            "reference that knowledge in your answers."
        )


llm_service = LLMService()
