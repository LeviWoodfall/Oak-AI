"""
Agent Memory — persistent long-term memory system.
Inspired by hermes-agent (user profile, self-improving) and deer-flow (dedup, task memory).
Stores memory as structured JSON with automatic deduplication.
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from backend.config import DATA_DIR

logger = logging.getLogger("oak.agent.memory")

MEMORY_DIR = DATA_DIR / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

USER_PROFILE_FILE = MEMORY_DIR / "user_profile.json"
TASK_MEMORY_FILE = MEMORY_DIR / "task_memory.json"
FACTS_FILE = MEMORY_DIR / "facts.json"
LEARNINGS_FILE = MEMORY_DIR / "learnings.json"


class AgentMemory:
    """Persistent memory across sessions. Knows who you are and what you've done."""

    def __init__(self):
        self._user_profile = self._load(USER_PROFILE_FILE, default={
            "name": "",
            "preferences": {},
            "tech_stack": [],
            "coding_style": "",
            "notes": [],
            "updated": "",
        })
        self._facts = self._load(FACTS_FILE, default=[])
        self._learnings = self._load(LEARNINGS_FILE, default=[])
        self._task_memory = self._load(TASK_MEMORY_FILE, default=[])

    # ── User Profile ─────────────────────────────────────────────────

    def get_profile(self) -> dict:
        return self._user_profile

    def update_profile(self, **kwargs) -> dict:
        """Update user profile fields."""
        for key, value in kwargs.items():
            if key in self._user_profile:
                self._user_profile[key] = value
        self._user_profile["updated"] = datetime.now(timezone.utc).isoformat()
        self._save(USER_PROFILE_FILE, self._user_profile)
        return self._user_profile

    def add_tech_stack(self, tech: str):
        if tech not in self._user_profile["tech_stack"]:
            self._user_profile["tech_stack"].append(tech)
            self._save(USER_PROFILE_FILE, self._user_profile)

    def add_preference(self, key: str, value: str):
        self._user_profile["preferences"][key] = value
        self._save(USER_PROFILE_FILE, self._user_profile)

    # ── Facts (deduped knowledge) ────────────────────────────────────

    def add_fact(self, fact: str, source: str = "conversation") -> bool:
        """Add a fact, skipping duplicates. Returns True if added."""
        normalised = fact.strip().lower()
        for existing in self._facts:
            if existing["text"].strip().lower() == normalised:
                return False
        self._facts.append({
            "text": fact.strip(),
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._facts) > 500:
            self._facts = self._facts[-500:]
        self._save(FACTS_FILE, self._facts)
        logger.info("Memory: added fact from %s", source)
        return True

    def get_facts(self, limit: int = 50) -> list[dict]:
        return self._facts[-limit:]

    def search_facts(self, query: str) -> list[dict]:
        """Simple keyword search across facts."""
        q = query.lower()
        return [f for f in self._facts if q in f["text"].lower()]

    # ── Learnings (self-improvement) ─────────────────────────────────

    def add_learning(self, learning: str, context: str = "") -> bool:
        """Record something the agent learned. Deduped."""
        normalised = learning.strip().lower()
        for existing in self._learnings:
            if existing["text"].strip().lower() == normalised:
                return False
        self._learnings.append({
            "text": learning.strip(),
            "context": context,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._learnings) > 200:
            self._learnings = self._learnings[-200:]
        self._save(LEARNINGS_FILE, self._learnings)
        return True

    def get_learnings(self, limit: int = 20) -> list[dict]:
        return self._learnings[-limit:]

    # ── Task Memory ──────────────────────────────────────────────────

    def record_task(self, task: str, result: str, success: bool, tools_used: list[str] = None):
        """Record a completed task for future reference."""
        self._task_memory.append({
            "task": task,
            "result": result[:500],
            "success": success,
            "tools_used": tools_used or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._task_memory) > 200:
            self._task_memory = self._task_memory[-200:]
        self._save(TASK_MEMORY_FILE, self._task_memory)

    def get_recent_tasks(self, limit: int = 10) -> list[dict]:
        return self._task_memory[-limit:]

    def get_similar_tasks(self, task: str) -> list[dict]:
        """Find tasks with similar keywords."""
        words = set(task.lower().split())
        scored = []
        for t in self._task_memory:
            task_words = set(t["task"].lower().split())
            overlap = len(words & task_words)
            if overlap > 0:
                scored.append((overlap, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:5]]

    # ── Context for LLM ──────────────────────────────────────────────

    def build_context(self) -> str:
        """Build a concise memory context string for the LLM system prompt."""
        parts = []

        # User profile
        p = self._user_profile
        if p.get("name"):
            parts.append(f"User: {p['name']}")
        if p.get("tech_stack"):
            parts.append(f"Tech stack: {', '.join(p['tech_stack'])}")
        if p.get("coding_style"):
            parts.append(f"Coding style: {p['coding_style']}")
        if p.get("preferences"):
            prefs = "; ".join(f"{k}: {v}" for k, v in p["preferences"].items())
            parts.append(f"Preferences: {prefs}")

        # Recent learnings
        recent_learnings = self._learnings[-5:]
        if recent_learnings:
            parts.append("Recent learnings:")
            for l in recent_learnings:
                parts.append(f"  - {l['text']}")

        # Recent task context
        recent_tasks = self._task_memory[-3:]
        if recent_tasks:
            parts.append("Recent tasks:")
            for t in recent_tasks:
                status = "✓" if t["success"] else "✗"
                parts.append(f"  {status} {t['task'][:80]}")

        return "\n".join(parts) if parts else ""

    # ── Stats ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "facts_count": len(self._facts),
            "learnings_count": len(self._learnings),
            "tasks_count": len(self._task_memory),
            "profile_set": bool(self._user_profile.get("name")),
        }

    # ── Persistence helpers ──────────────────────────────────────────

    @staticmethod
    def _load(filepath: Path, default=None):
        if filepath.exists():
            try:
                return json.loads(filepath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("Failed to load %s: %s", filepath.name, e)
        return default if default is not None else {}

    @staticmethod
    def _save(filepath: Path, data):
        filepath.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


agent_memory = AgentMemory()
