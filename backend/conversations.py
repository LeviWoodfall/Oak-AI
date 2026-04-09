"""
Conversation persistence — stores chat history as JSON files.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from backend.config import CONVERSATIONS_DIR

logger = logging.getLogger("oak.conversations")


class ConversationManager:
    """Manages chat conversation history."""

    def __init__(self):
        CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    def create(self, title: Optional[str] = None) -> dict:
        """Create a new conversation."""
        conv_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        conv = {
            "id": conv_id,
            "title": title or f"Chat {conv_id}",
            "created": now,
            "updated": now,
            "messages": [],
        }
        self._save(conv)
        return conv

    def get(self, conv_id: str) -> Optional[dict]:
        """Load a conversation by ID."""
        filepath = CONVERSATIONS_DIR / f"{conv_id}.json"
        if not filepath.exists():
            return None
        return json.loads(filepath.read_text(encoding="utf-8"))

    def add_message(self, conv_id: str, role: str, content: str) -> dict:
        """Add a message to a conversation."""
        conv = self.get(conv_id)
        if not conv:
            conv = self.create()
            conv_id = conv["id"]

        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        conv["messages"].append(msg)
        conv["updated"] = msg["timestamp"]

        # Auto-title from first user message
        if role == "user" and len(conv["messages"]) == 1:
            conv["title"] = content[:60] + ("..." if len(content) > 60 else "")

        self._save(conv)
        return msg

    def list_all(self) -> list[dict]:
        """List all conversations (without messages)."""
        convs = []
        for f in sorted(CONVERSATIONS_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                convs.append({
                    "id": data["id"],
                    "title": data["title"],
                    "created": data["created"],
                    "updated": data["updated"],
                    "message_count": len(data["messages"]),
                })
            except Exception as e:
                logger.warning("Error reading %s: %s", f.name, e)
        convs.sort(key=lambda c: c["updated"], reverse=True)
        return convs

    def delete(self, conv_id: str) -> bool:
        filepath = CONVERSATIONS_DIR / f"{conv_id}.json"
        if filepath.exists():
            filepath.unlink()
            return True
        return False

    def _save(self, conv: dict):
        filepath = CONVERSATIONS_DIR / f"{conv['id']}.json"
        filepath.write_text(json.dumps(conv, indent=2), encoding="utf-8")


conversation_manager = ConversationManager()
