"""
Joplin integration — connects to Joplin's Data API for note-taking with AI.
Joplin desktop must be running with the Web Clipper service enabled (port 41184).
Token is set via JOPLIN_TOKEN env var or Settings page.

Supports: notes CRUD, notebooks (folders), tags, search, and wiki sync.
"""
import logging
import os
from typing import Optional
import httpx
from backend.config import settings

logger = logging.getLogger("oak.joplin")

DEFAULT_FIELDS_NOTE = "id,parent_id,title,body,updated_time,created_time,is_todo,todo_completed"
DEFAULT_FIELDS_FOLDER = "id,parent_id,title"
DEFAULT_FIELDS_TAG = "id,title"


class JoplinService:
    """Client for the Joplin Data API (REST, localhost:41184)."""

    def __init__(self):
        self._base_url = settings.joplin_url
        self._token = settings.joplin_token
        self._client = httpx.AsyncClient(timeout=15)

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def set_token(self, token: str):
        self._token = token
        os.environ["JOPLIN_TOKEN"] = token

    def _params(self, extra: dict = None) -> dict:
        p = {"token": self._token}
        if extra:
            p.update(extra)
        return p

    # ── Health ────────────────────────────────────────────────────────

    async def ping(self) -> dict:
        """Check if Joplin is running and reachable."""
        try:
            resp = await self._client.get(f"{self._base_url}/ping")
            running = resp.text.strip().startswith("Joplin")
            return {"connected": running, "configured": self.configured, "response": resp.text.strip()}
        except Exception as e:
            return {"connected": False, "configured": self.configured, "error": str(e)}

    # ── Notebooks (Folders) ───────────────────────────────────────────

    async def list_notebooks(self) -> list[dict]:
        """List all notebooks."""
        return await self._paginate("/folders", fields=DEFAULT_FIELDS_FOLDER)

    async def get_notebook(self, notebook_id: str) -> Optional[dict]:
        try:
            resp = await self._client.get(
                f"{self._base_url}/folders/{notebook_id}",
                params=self._params({"fields": DEFAULT_FIELDS_FOLDER}),
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    async def create_notebook(self, title: str, parent_id: str = "") -> dict:
        """Create a new notebook."""
        data = {"title": title}
        if parent_id:
            data["parent_id"] = parent_id
        resp = await self._client.post(
            f"{self._base_url}/folders",
            params=self._params(),
            json=data,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_notebook_notes(self, notebook_id: str) -> list[dict]:
        """Get all notes in a notebook."""
        return await self._paginate(
            f"/folders/{notebook_id}/notes",
            fields="id,parent_id,title,updated_time,is_todo,todo_completed",
            order_by="updated_time",
            order_dir="DESC",
        )

    # ── Notes ─────────────────────────────────────────────────────────

    async def list_notes(self, limit: int = 50) -> list[dict]:
        """List recent notes."""
        return await self._paginate(
            "/notes",
            fields="id,parent_id,title,updated_time,is_todo,todo_completed",
            order_by="updated_time",
            order_dir="DESC",
            limit=limit,
        )

    async def get_note(self, note_id: str) -> Optional[dict]:
        """Get a note by ID with full body."""
        try:
            resp = await self._client.get(
                f"{self._base_url}/notes/{note_id}",
                params=self._params({"fields": DEFAULT_FIELDS_NOTE}),
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    async def create_note(self, title: str, body: str, notebook_id: str = "",
                          tags: list[str] = None, is_todo: bool = False) -> dict:
        """Create a new note (markdown body)."""
        data = {"title": title, "body": body}
        if notebook_id:
            data["parent_id"] = notebook_id
        if is_todo:
            data["is_todo"] = 1
        resp = await self._client.post(
            f"{self._base_url}/notes",
            params=self._params(),
            json=data,
        )
        resp.raise_for_status()
        note = resp.json()

        # Attach tags
        if tags:
            for tag_name in tags:
                tag = await self._ensure_tag(tag_name)
                if tag:
                    await self._tag_note(tag["id"], note["id"])

        return note

    async def update_note(self, note_id: str, title: str = None,
                          body: str = None, is_todo: bool = None) -> dict:
        """Update an existing note."""
        data = {}
        if title is not None:
            data["title"] = title
        if body is not None:
            data["body"] = body
        if is_todo is not None:
            data["is_todo"] = 1 if is_todo else 0
        resp = await self._client.put(
            f"{self._base_url}/notes/{note_id}",
            params=self._params(),
            json=data,
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_note(self, note_id: str) -> bool:
        """Move a note to trash."""
        try:
            resp = await self._client.delete(
                f"{self._base_url}/notes/{note_id}",
                params=self._params(),
            )
            return resp.status_code < 400
        except Exception:
            return False

    # ── Search ────────────────────────────────────────────────────────

    async def search_notes(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across all notes."""
        try:
            resp = await self._client.get(
                f"{self._base_url}/search",
                params=self._params({
                    "query": query,
                    "fields": "id,parent_id,title,body,updated_time",
                    "limit": str(limit),
                }),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("items", [])
        except Exception as e:
            logger.error("Joplin search failed: %s", e)
            return []

    # ── Tags ──────────────────────────────────────────────────────────

    async def list_tags(self) -> list[dict]:
        return await self._paginate("/tags", fields=DEFAULT_FIELDS_TAG)

    async def get_note_tags(self, note_id: str) -> list[dict]:
        try:
            resp = await self._client.get(
                f"{self._base_url}/notes/{note_id}/tags",
                params=self._params({"fields": DEFAULT_FIELDS_TAG}),
            )
            resp.raise_for_status()
            return resp.json().get("items", [])
        except Exception:
            return []

    async def _ensure_tag(self, tag_name: str) -> Optional[dict]:
        """Get or create a tag by name."""
        try:
            resp = await self._client.get(
                f"{self._base_url}/search",
                params=self._params({"query": tag_name, "type": "tag"}),
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            for item in items:
                if item.get("title", "").lower() == tag_name.lower():
                    return item
            # Create new tag
            resp = await self._client.post(
                f"{self._base_url}/tags",
                params=self._params(),
                json={"title": tag_name},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    async def _tag_note(self, tag_id: str, note_id: str):
        try:
            await self._client.post(
                f"{self._base_url}/tags/{tag_id}/notes",
                params=self._params(),
                json={"id": note_id},
            )
        except Exception:
            pass

    # ── Wiki Sync ─────────────────────────────────────────────────────

    async def sync_note_to_wiki(self, note_id: str) -> Optional[dict]:
        """Import a Joplin note into the Oak wiki."""
        from backend.wiki_service import wiki_service
        note = await self.get_note(note_id)
        if not note:
            return None
        tags = await self.get_note_tags(note_id)
        tag_names = [t["title"] for t in tags] + ["joplin-import"]
        article = wiki_service.create_article(
            title=note["title"],
            content=note.get("body", ""),
            tags=tag_names,
        )
        return article

    async def sync_wiki_to_note(self, wiki_slug: str, notebook_id: str = "") -> Optional[dict]:
        """Export a Oak wiki article to Joplin."""
        from backend.wiki_service import wiki_service
        article = wiki_service.get_article(wiki_slug)
        if not article:
            return None
        note = await self.create_note(
            title=article["title"],
            body=article["content"],
            notebook_id=notebook_id,
            tags=article.get("tags", []) + ["codepilot-export"],
        )
        return note

    async def ensure_oak_notebook(self) -> dict:
        """Get or create the 'Oak' notebook for agent notes."""
        notebooks = await self.list_notebooks()
        for nb in notebooks:
            if nb.get("title", "").lower() in ("oak", "codepilot"):
                return nb
        return await self.create_notebook("Oak")

    # ── AI Note-taking ────────────────────────────────────────────────

    async def save_ai_note(self, title: str, content: str, tags: list[str] = None) -> dict:
        """Save a note from the AI agent into the Oak notebook."""
        nb = await self.ensure_oak_notebook()
        return await self.create_note(
            title=title,
            body=content,
            notebook_id=nb["id"],
            tags=(tags or []) + ["ai-generated"],
        )

    async def save_chat_summary(self, conversation_title: str, summary: str) -> dict:
        """Save a chat conversation summary as a Joplin note."""
        nb = await self.ensure_oak_notebook()
        return await self.create_note(
            title=f"Chat: {conversation_title}",
            body=summary,
            notebook_id=nb["id"],
            tags=["chat-summary", "ai-generated"],
        )

    # ── Pagination helper ─────────────────────────────────────────────

    async def _paginate(self, endpoint: str, fields: str = "", limit: int = 100,
                        order_by: str = "", order_dir: str = "", max_pages: int = 5) -> list[dict]:
        """Fetch paginated results from Joplin API."""
        all_items = []
        page = 1
        while page <= max_pages:
            params = self._params({"page": str(page), "limit": str(min(limit, 100))})
            if fields:
                params["fields"] = fields
            if order_by:
                params["order_by"] = order_by
            if order_dir:
                params["order_dir"] = order_dir
            try:
                resp = await self._client.get(f"{self._base_url}{endpoint}", params=params)
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                all_items.extend(items)
                if not data.get("has_more") or len(all_items) >= limit:
                    break
                page += 1
            except Exception as e:
                logger.error("Joplin pagination error on %s: %s", endpoint, e)
                break
        return all_items[:limit]


joplin_service = JoplinService()
