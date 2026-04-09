"""
Audit Logger — immutable append-only log of all agent actions.
Every skill acquisition, auto-research, code change, workflow run, and
self-improvement action is logged with timestamp, source, and details.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from backend.config import DATA_DIR

logger = logging.getLogger("oak.agent.audit")

AUDIT_DIR = DATA_DIR / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG = AUDIT_DIR / "audit.jsonl"


class AuditLogger:
    """Append-only structured audit log for all agent activity."""

    # Action categories
    SKILL_INSTALLED = "skill_installed"
    SKILL_CREATED = "skill_created"
    SKILL_UPDATED = "skill_updated"
    SKILL_DELETED = "skill_deleted"
    SELF_RESEARCH = "self_research"
    SELF_IMPROVE = "self_improve"
    WORKFLOW_RUN = "workflow_run"
    WORKFLOW_CREATED = "workflow_created"
    TOOL_CALL = "tool_call"
    NOTE_CREATED = "note_created"
    WIKI_CREATED = "wiki_created"
    CODE_CHANGE = "code_change"
    CONFIG_CHANGE = "config_change"
    ERROR = "error"

    def log(self, action: str, summary: str, details: dict = None, source: str = "agent"):
        """Append an audit entry. Never modifies existing entries."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "summary": summary,
            "source": source,
            "details": details or {},
        }
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        logger.info("[AUDIT] %s: %s", action, summary)

    def get_recent(self, limit: int = 50, action_filter: str = None) -> list[dict]:
        """Read recent audit entries (newest first)."""
        if not AUDIT_LOG.exists():
            return []
        entries = []
        for line in AUDIT_LOG.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if action_filter and entry.get("action") != action_filter:
                    continue
                entries.append(entry)
            except json.JSONDecodeError:
                continue
        entries.reverse()
        return entries[:limit]

    def get_daily_summary(self, date_str: str = None) -> dict:
        """Summary of actions for a given day (default: today)."""
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entries = self.get_recent(limit=500)
        day_entries = [e for e in entries if e["timestamp"].startswith(date_str)]
        counts = {}
        for e in day_entries:
            counts[e["action"]] = counts.get(e["action"], 0) + 1
        return {
            "date": date_str,
            "total_actions": len(day_entries),
            "by_type": counts,
            "entries": day_entries[:20],
        }

    def search(self, query: str, limit: int = 30) -> list[dict]:
        """Search audit log by keyword."""
        q = query.lower()
        entries = self.get_recent(limit=500)
        return [
            e for e in entries
            if q in e.get("summary", "").lower() or q in json.dumps(e.get("details", {})).lower()
        ][:limit]


audit_log = AuditLogger()
