"""
Agent Memory — hermes-agent-inspired persistent memory system.

Dual-store architecture (from hermes-agent):
  MEMORY.md — Agent's personal notes (environment, lessons, conventions)
  USER.md   — User profile (name, preferences, communication style)

Key patterns adapted from hermes-agent:
  - Frozen snapshot: injected into system prompt at session start, immutable during session
  - Capacity management: hard char limits with usage %, consolidation at >80%
  - Memory tool: add/replace/remove actions (agent manages its own memory)
  - Security scanning: blocks injection patterns before storing
  - Duplicate prevention: rejects exact duplicates
  - Session search: past conversations searchable via SQLite FTS
"""
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from backend.config import DATA_DIR

logger = logging.getLogger("oak.agent.memory")

MEMORY_DIR = DATA_DIR / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_FILE = MEMORY_DIR / "MEMORY.md"   # Agent's personal notes
USER_FILE = MEMORY_DIR / "USER.md"       # User profile
TASK_MEMORY_FILE = MEMORY_DIR / "task_memory.json"
SESSION_DB = MEMORY_DIR / "sessions.db"

# Hermes-style character limits (~tokens = chars / 2.75)
MEMORY_CHAR_LIMIT = 2200   # ~800 tokens
USER_CHAR_LIMIT = 1375     # ~500 tokens
ENTRY_SEPARATOR = "§"

# Security: patterns to reject from memory entries
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"you\s+are\s+now",
    r"system\s*:\s*",
    r"<\|im_start\|>",
    r"BEGIN\s+INJECTION",
    r"ssh\s+.*@.*\s+-p",
    r"curl\s+.*\|\s*bash",
    r"eval\s*\(",
]


class AgentMemory:
    """Hermes-inspired dual-store persistent memory with capacity management."""

    def __init__(self):
        # Load dual stores from markdown files (hermes pattern)
        self._memory_entries = self._load_md(MEMORY_FILE)
        self._user_entries = self._load_md(USER_FILE)
        self._task_memory = self._load_json(TASK_MEMORY_FILE, default=[])
        self._frozen_snapshot = None  # Set once per session
        self._init_session_db()

    # ── Memory Tool Actions (hermes pattern: add/replace/remove) ────

    def memory_add(self, target: str, text: str) -> dict:
        """Add an entry to memory or user store. Returns success/error."""
        if not self._security_check(text):
            return {"success": False, "error": "Entry blocked by security scan"}

        entries = self._memory_entries if target == "memory" else self._user_entries
        limit = MEMORY_CHAR_LIMIT if target == "memory" else USER_CHAR_LIMIT

        # Duplicate check
        if text.strip() in [e.strip() for e in entries]:
            return {"success": True, "note": "No duplicate added"}

        current_size = sum(len(e) for e in entries)
        if current_size + len(text) > limit:
            return {
                "success": False,
                "error": f"Memory at {current_size}/{limit} chars. Adding {len(text)} chars would exceed limit. Replace or remove entries first.",
                "current_entries": entries,
                "usage": f"{current_size}/{limit}",
            }

        entries.append(text.strip())
        self._save_store(target)
        return {"success": True, "usage": f"{current_size + len(text)}/{limit}"}

    def memory_replace(self, target: str, old_text: str, new_text: str) -> dict:
        """Replace an entry using substring matching (hermes pattern)."""
        if not self._security_check(new_text):
            return {"success": False, "error": "Entry blocked by security scan"}

        entries = self._memory_entries if target == "memory" else self._user_entries
        for i, entry in enumerate(entries):
            if old_text in entry:
                entries[i] = entry.replace(old_text, new_text)
                self._save_store(target)
                return {"success": True, "replaced": True}
        return {"success": False, "error": "old_text not found in any entry"}

    def memory_remove(self, target: str, old_text: str) -> dict:
        """Remove an entry using substring matching."""
        entries = self._memory_entries if target == "memory" else self._user_entries
        for i, entry in enumerate(entries):
            if old_text in entry:
                entries.pop(i)
                self._save_store(target)
                return {"success": True, "removed": True}
        return {"success": False, "error": "old_text not found in any entry"}

    # ── Frozen Snapshot (hermes pattern) ──────────────────────────────

    def build_context(self) -> str:
        """Build the frozen memory snapshot for the system prompt.
        Called once at session start, never changes mid-session (hermes pattern)."""
        if self._frozen_snapshot is not None:
            return self._frozen_snapshot

        parts = []

        # MEMORY block
        mem_text = ENTRY_SEPARATOR.join(self._memory_entries)
        mem_size = len(mem_text)
        mem_pct = round(mem_size / MEMORY_CHAR_LIMIT * 100) if MEMORY_CHAR_LIMIT else 0
        if self._memory_entries:
            parts.append(
                f"══ MEMORY (your personal notes) [{mem_pct}% — {mem_size}/{MEMORY_CHAR_LIMIT} chars] ══\n"
                + mem_text
            )

        # USER block
        user_text = ENTRY_SEPARATOR.join(self._user_entries)
        user_size = len(user_text)
        user_pct = round(user_size / USER_CHAR_LIMIT * 100) if USER_CHAR_LIMIT else 0
        if self._user_entries:
            parts.append(
                f"══ USER PROFILE [{user_pct}% — {user_size}/{USER_CHAR_LIMIT} chars] ══\n"
                + user_text
            )

        # Recent tasks
        recent = self._task_memory[-3:]
        if recent:
            task_lines = []
            for t in recent:
                status = "✓" if t.get("success") else "✗"
                task_lines.append(f"  {status} {t['task'][:80]}")
            parts.append("Recent tasks:\n" + "\n".join(task_lines))

        self._frozen_snapshot = "\n\n".join(parts) if parts else ""
        return self._frozen_snapshot

    def reset_snapshot(self):
        """Reset the frozen snapshot (call at session start)."""
        self._frozen_snapshot = None

    # ── Backward-compatible API (used by main.py endpoints) ──────────

    def get_profile(self) -> dict:
        return {"entries": self._user_entries, "usage": f"{sum(len(e) for e in self._user_entries)}/{USER_CHAR_LIMIT}"}

    def update_profile(self, **kwargs) -> dict:
        """Add key=value pairs to user profile store."""
        for key, value in kwargs.items():
            if value:
                self.memory_add("user", f"{key}: {value}")
        return self.get_profile()

    def add_fact(self, fact: str, source: str = "conversation") -> bool:
        result = self.memory_add("memory", fact)
        return result.get("success", False)

    def get_facts(self, limit: int = 50) -> list[dict]:
        return [{"text": e, "source": "memory"} for e in self._memory_entries[-limit:]]

    def search_facts(self, query: str) -> list[dict]:
        q = query.lower()
        return [{"text": e, "source": "memory"} for e in self._memory_entries if q in e.lower()]

    def get_learnings(self, limit: int = 20) -> list[dict]:
        return [{"text": e} for e in self._memory_entries[-limit:] if "learned" in e.lower() or "lesson" in e.lower()]

    # ── Task Memory ──────────────────────────────────────────────────

    def record_task(self, task: str, result: str, success: bool, tools_used: list[str] = None):
        self._task_memory.append({
            "task": task, "result": result[:500], "success": success,
            "tools_used": tools_used or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._task_memory) > 200:
            self._task_memory = self._task_memory[-200:]
        self._save_json(TASK_MEMORY_FILE, self._task_memory)

    def get_recent_tasks(self, limit: int = 10) -> list[dict]:
        return self._task_memory[-limit:]

    # ── Session Search (hermes pattern: SQLite FTS5) ──────────────────

    def _init_session_db(self):
        try:
            conn = sqlite3.connect(str(SESSION_DB))
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS session_search
                USING fts5(session_id, role, content, timestamp)
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Session DB init failed: %s", e)

    def save_session_message(self, session_id: str, role: str, content: str):
        """Save a conversation message for future search."""
        try:
            conn = sqlite3.connect(str(SESSION_DB))
            conn.execute(
                "INSERT INTO session_search(session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (session_id, role, content[:2000], datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Session save failed: %s", e)

    def search_sessions(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across past conversations."""
        try:
            conn = sqlite3.connect(str(SESSION_DB))
            rows = conn.execute(
                "SELECT session_id, role, content, timestamp FROM session_search WHERE content MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
            conn.close()
            return [{"session_id": r[0], "role": r[1], "content": r[2][:300], "timestamp": r[3]} for r in rows]
        except Exception as e:
            logger.warning("Session search failed: %s", e)
            return []

    # ── Security Scanning (hermes pattern) ────────────────────────────

    @staticmethod
    def _security_check(text: str) -> bool:
        """Block injection/exfiltration patterns from entering memory."""
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                logger.warning("Memory entry blocked by security scan: matched pattern %s", pattern)
                return False
        return True

    # ── Stats ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        mem_size = sum(len(e) for e in self._memory_entries)
        user_size = sum(len(e) for e in self._user_entries)
        return {
            "memory_entries": len(self._memory_entries),
            "memory_usage": f"{mem_size}/{MEMORY_CHAR_LIMIT} ({round(mem_size/MEMORY_CHAR_LIMIT*100)}%)" if MEMORY_CHAR_LIMIT else "0",
            "user_entries": len(self._user_entries),
            "user_usage": f"{user_size}/{USER_CHAR_LIMIT} ({round(user_size/USER_CHAR_LIMIT*100)}%)" if USER_CHAR_LIMIT else "0",
            "tasks_count": len(self._task_memory),
        }

    # ── Persistence helpers ──────────────────────────────────────────

    @staticmethod
    def _load_md(filepath: Path) -> list[str]:
        """Load entries from a markdown file (§ separated)."""
        if filepath.exists():
            text = filepath.read_text(encoding="utf-8").strip()
            if text:
                return [e.strip() for e in text.split(ENTRY_SEPARATOR) if e.strip()]
        return []

    def _save_store(self, target: str):
        entries = self._memory_entries if target == "memory" else self._user_entries
        filepath = MEMORY_FILE if target == "memory" else USER_FILE
        filepath.write_text(ENTRY_SEPARATOR.join(entries), encoding="utf-8")

    @staticmethod
    def _load_json(filepath: Path, default=None):
        if filepath.exists():
            try:
                return json.loads(filepath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, Exception):
                pass
        return default if default is not None else []

    @staticmethod
    def _save_json(filepath: Path, data):
        filepath.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


agent_memory = AgentMemory()
