"""
Tiered Context Engine — OpenViking-inspired L0/L1/L2 context management.
Replaces flat vector chunks with a hierarchical loading strategy:
  L0 (Abstract): ~100 tokens — one-sentence summary for quick relevance check
  L1 (Overview): ~500 tokens — core info for planning decisions
  L2 (Details):  full content — loaded only when the agent needs deep detail

This dramatically reduces token consumption by loading context progressively.
"""
import json
import logging
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from backend.config import DATA_DIR

logger = logging.getLogger("oak.context")

CONTEXT_DIR = DATA_DIR / "context"
CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
INDEX_FILE = CONTEXT_DIR / "index.json"


class ContextEntry:
    """A single context item with L0/L1/L2 tiers."""

    def __init__(self, uri: str, title: str, l0: str, l1: str, l2: str,
                 source: str = "", tags: list[str] = None, updated: str = ""):
        self.uri = uri
        self.title = title
        self.l0 = l0  # ~1 sentence abstract
        self.l1 = l1  # ~500 token overview
        self.l2 = l2  # full content
        self.source = source
        self.tags = tags or []
        self.updated = updated or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "uri": self.uri,
            "title": self.title,
            "l0": self.l0,
            "l1": self.l1,
            "source": self.source,
            "tags": self.tags,
            "updated": self.updated,
            "l2_length": len(self.l2),
        }

    def get_tier(self, level: int = 0) -> str:
        if level == 0:
            return self.l0
        elif level == 1:
            return self.l1
        return self.l2


class TieredContextEngine:
    """Manages context with progressive L0/L1/L2 loading."""

    def __init__(self):
        self._entries: dict[str, ContextEntry] = {}
        self._load_index()

    def _load_index(self):
        if INDEX_FILE.exists():
            try:
                data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
                for uri, entry_data in data.items():
                    l2_file = CONTEXT_DIR / f"{self._hash(uri)}.l2.txt"
                    l2 = l2_file.read_text(encoding="utf-8") if l2_file.exists() else ""
                    self._entries[uri] = ContextEntry(
                        uri=uri, title=entry_data.get("title", ""),
                        l0=entry_data.get("l0", ""), l1=entry_data.get("l1", ""),
                        l2=l2, source=entry_data.get("source", ""),
                        tags=entry_data.get("tags", []), updated=entry_data.get("updated", ""),
                    )
            except Exception as e:
                logger.warning("Failed to load context index: %s", e)
        logger.info("Loaded %d context entries", len(self._entries))

    def _save_index(self):
        data = {}
        for uri, entry in self._entries.items():
            data[uri] = {
                "title": entry.title, "l0": entry.l0, "l1": entry.l1,
                "source": entry.source, "tags": entry.tags, "updated": entry.updated,
            }
        INDEX_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _hash(uri: str) -> str:
        return hashlib.md5(uri.encode()).hexdigest()[:16]

    # ── Ingest ───────────────────────────────────────────────────────

    def ingest(self, uri: str, title: str, content: str, source: str = "",
               tags: list[str] = None) -> ContextEntry:
        """Ingest content and auto-generate L0/L1/L2 tiers."""
        l0 = self._generate_l0(title, content)
        l1 = self._generate_l1(content)
        l2 = content

        entry = ContextEntry(uri=uri, title=title, l0=l0, l1=l1, l2=l2,
                              source=source, tags=tags or [])
        self._entries[uri] = entry

        # Save L2 to separate file (keeps index small)
        l2_file = CONTEXT_DIR / f"{self._hash(uri)}.l2.txt"
        l2_file.write_text(l2, encoding="utf-8")
        self._save_index()

        logger.info("Ingested context: %s (%d chars)", uri, len(content))
        return entry

    def ingest_from_wiki(self, slug: str, title: str, content: str):
        """Ingest a wiki article."""
        return self.ingest(f"oak://wiki/{slug}", title, content, source="wiki", tags=["wiki"])

    def ingest_from_code(self, repo: str, filepath: str, content: str):
        """Ingest a code file."""
        return self.ingest(f"oak://code/{repo}/{filepath}", filepath, content,
                           source=f"repo:{repo}", tags=["code", repo])

    def ingest_from_note(self, note_id: str, title: str, content: str):
        """Ingest a Joplin note."""
        return self.ingest(f"oak://notes/{note_id}", title, content, source="joplin", tags=["note"])

    # ── Retrieval ────────────────────────────────────────────────────

    def search(self, query: str, max_results: int = 10, tier: int = 0) -> list[dict]:
        """Search context entries by keyword, returning the specified tier."""
        q = query.lower()
        scored = []
        for uri, entry in self._entries.items():
            # Score by keyword match across title, tags, L0, L1
            text = f"{entry.title} {' '.join(entry.tags)} {entry.l0} {entry.l1}".lower()
            words = q.split()
            score = sum(1 for w in words if w in text)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"uri": e.uri, "title": e.title, "content": e.get_tier(tier),
             "score": s, "source": e.source, "tags": e.tags}
            for s, e in scored[:max_results]
        ]

    def get(self, uri: str, tier: int = 2) -> Optional[dict]:
        """Get a specific context entry at the given tier."""
        entry = self._entries.get(uri)
        if not entry:
            return None
        return {"uri": uri, "title": entry.title, "content": entry.get_tier(tier),
                "source": entry.source, "tags": entry.tags}

    def build_context_for_query(self, query: str, token_budget: int = 2000) -> str:
        """Build optimal context string within a token budget.
        Starts with L0 for all matches, promotes to L1/L2 for best matches."""
        matches = self.search(query, max_results=20, tier=0)
        if not matches:
            return ""

        context_parts = []
        tokens_used = 0

        # First pass: L0 for top matches
        for m in matches[:10]:
            l0_text = f"[{m['title']}] {m['content']}"
            est_tokens = len(l0_text) // 4
            if tokens_used + est_tokens > token_budget:
                break
            context_parts.append(l0_text)
            tokens_used += est_tokens

        # Second pass: promote top 3 to L1
        for m in matches[:3]:
            entry = self._entries.get(m["uri"])
            if not entry:
                continue
            l1_text = f"[{entry.title} - Detail]\n{entry.l1}"
            est_tokens = len(l1_text) // 4
            if tokens_used + est_tokens > token_budget:
                break
            context_parts.append(l1_text)
            tokens_used += est_tokens

        return "\n\n".join(context_parts)

    # ── Stats & Management ───────────────────────────────────────────

    def list_all(self, source_filter: str = None) -> list[dict]:
        entries = []
        for uri, entry in self._entries.items():
            if source_filter and entry.source != source_filter:
                continue
            entries.append(entry.to_dict())
        return entries

    def remove(self, uri: str) -> bool:
        if uri not in self._entries:
            return False
        del self._entries[uri]
        l2_file = CONTEXT_DIR / f"{self._hash(uri)}.l2.txt"
        l2_file.unlink(missing_ok=True)
        self._save_index()
        return True

    def stats(self) -> dict:
        sources = {}
        for entry in self._entries.values():
            sources[entry.source] = sources.get(entry.source, 0) + 1
        return {"total_entries": len(self._entries), "by_source": sources}

    # ── Tier generation (simple heuristics, upgradeable to LLM) ─────

    @staticmethod
    def _generate_l0(title: str, content: str) -> str:
        """Generate L0 abstract (~1 sentence)."""
        first_line = content.strip().split("\n")[0][:150] if content else ""
        return f"{title}: {first_line}"

    @staticmethod
    def _generate_l1(content: str, max_chars: int = 800) -> str:
        """Generate L1 overview (~500 tokens). Takes first meaningful paragraphs."""
        if len(content) <= max_chars:
            return content
        paragraphs = content.split("\n\n")
        result = ""
        for p in paragraphs:
            if len(result) + len(p) > max_chars:
                break
            result += p + "\n\n"
        return result.strip() or content[:max_chars]


tiered_context = TieredContextEngine()
