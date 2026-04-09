"""
OneNote Integration — Microsoft Graph API client for note-taking with Oak.
Uses OAuth2 Device Code Flow (no browser redirect needed) via MSAL.

OneNote structure: Notebooks → Sections → Pages
Pages contain HTML content (OneNote uses HTML, not markdown).

Requires: pip install msal
Set env vars: MS_CLIENT_ID (Azure AD app registration client ID)
"""
import json
import logging
import os
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import httpx
from backend.config import DATA_DIR

logger = logging.getLogger("oak.onenote")

TOKEN_CACHE_FILE = DATA_DIR / "onenote_token_cache.json"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_BETA = "https://graph.microsoft.com/beta"
SCOPES = ["Notes.ReadWrite", "Notes.Create", "User.Read"]


class OneNoteService:
    """Microsoft Graph API client for OneNote integration."""

    def __init__(self):
        self._client_id = os.environ.get("MS_CLIENT_ID", "")
        self._access_token = ""
        self._token_expiry = 0
        self._msal_app = None
        self._accounts = None
        self._http = httpx.AsyncClient(timeout=30)
        self._load_cached_token()

    @property
    def configured(self) -> bool:
        return bool(self._client_id)

    @property
    def authenticated(self) -> bool:
        return bool(self._access_token)

    def set_client_id(self, client_id: str):
        self._client_id = client_id
        os.environ["MS_CLIENT_ID"] = client_id

    # ── Authentication (Device Code Flow) ─────────────────────────────

    def start_device_flow(self) -> dict:
        """Start OAuth2 device code flow. Returns user_code + verification_uri."""
        if not self._client_id:
            return {"error": "MS_CLIENT_ID not set. Register an app at https://portal.azure.com"}
        try:
            import msal
            self._msal_app = msal.PublicClientApplication(
                self._client_id,
                authority="https://login.microsoftonline.com/common",
            )
            flow = self._msal_app.initiate_device_flow(scopes=SCOPES)
            if "error" in flow:
                return {"error": flow.get("error_description", flow["error"])}
            return {
                "user_code": flow.get("user_code", ""),
                "verification_uri": flow.get("verification_uri", ""),
                "message": flow.get("message", ""),
                "flow": flow,
            }
        except ImportError:
            return {"error": "msal not installed. Run: pip install msal"}
        except Exception as e:
            return {"error": str(e)}

    def complete_device_flow(self, flow: dict) -> dict:
        """Complete the device code flow after user authorizes."""
        if not self._msal_app:
            return {"error": "No active device flow. Call start_device_flow first."}
        try:
            result = self._msal_app.acquire_token_by_device_flow(flow)
            if "access_token" in result:
                self._access_token = result["access_token"]
                self._save_token(result)
                return {"authenticated": True, "user": result.get("id_token_claims", {}).get("name", "")}
            return {"error": result.get("error_description", "Authentication failed")}
        except Exception as e:
            return {"error": str(e)}

    def _load_cached_token(self):
        """Try to load a cached token."""
        if TOKEN_CACHE_FILE.exists():
            try:
                data = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
                self._access_token = data.get("access_token", "")
                self._client_id = data.get("client_id", self._client_id)
            except Exception:
                pass

    def _save_token(self, result: dict):
        TOKEN_CACHE_FILE.write_text(json.dumps({
            "access_token": result.get("access_token", ""),
            "client_id": self._client_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }), encoding="utf-8")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    # ── Health ────────────────────────────────────────────────────────

    async def ping(self) -> dict:
        if not self._access_token:
            return {"connected": False, "configured": self.configured, "error": "Not authenticated"}
        try:
            resp = await self._http.get(f"{GRAPH_BASE}/me", headers=self._headers())
            if resp.status_code == 200:
                user = resp.json()
                return {"connected": True, "configured": True, "user": user.get("displayName", "")}
            return {"connected": False, "configured": True, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"connected": False, "configured": self.configured, "error": str(e)}

    # ── Notebooks ─────────────────────────────────────────────────────

    async def list_notebooks(self) -> list[dict]:
        try:
            resp = await self._http.get(
                f"{GRAPH_BASE}/me/onenote/notebooks",
                headers=self._headers(),
                params={"$orderby": "lastModifiedDateTime desc", "$top": "50"},
            )
            resp.raise_for_status()
            return [
                {"id": nb["id"], "title": nb.get("displayName", ""), "created": nb.get("createdDateTime", "")}
                for nb in resp.json().get("value", [])
            ]
        except Exception as e:
            logger.error("List notebooks failed: %s", e)
            return []

    async def create_notebook(self, title: str) -> dict:
        resp = await self._http.post(
            f"{GRAPH_BASE}/me/onenote/notebooks",
            headers=self._headers(),
            json={"displayName": title},
        )
        resp.raise_for_status()
        return resp.json()

    # ── Sections ──────────────────────────────────────────────────────

    async def list_sections(self, notebook_id: str) -> list[dict]:
        try:
            resp = await self._http.get(
                f"{GRAPH_BASE}/me/onenote/notebooks/{notebook_id}/sections",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return [
                {"id": s["id"], "title": s.get("displayName", ""), "notebook_id": notebook_id}
                for s in resp.json().get("value", [])
            ]
        except Exception as e:
            logger.error("List sections failed: %s", e)
            return []

    async def create_section(self, notebook_id: str, title: str) -> dict:
        resp = await self._http.post(
            f"{GRAPH_BASE}/me/onenote/notebooks/{notebook_id}/sections",
            headers=self._headers(),
            json={"displayName": title},
        )
        resp.raise_for_status()
        return resp.json()

    # ── Pages ─────────────────────────────────────────────────────────

    async def list_pages(self, section_id: str = "", limit: int = 50) -> list[dict]:
        """List pages in a section, or all recent pages if no section."""
        try:
            if section_id:
                url = f"{GRAPH_BASE}/me/onenote/sections/{section_id}/pages"
            else:
                url = f"{GRAPH_BASE}/me/onenote/pages"
            resp = await self._http.get(
                url, headers=self._headers(),
                params={"$orderby": "lastModifiedDateTime desc", "$top": str(limit),
                        "$select": "id,title,createdDateTime,lastModifiedDateTime,parentSection"},
            )
            resp.raise_for_status()
            return [
                {
                    "id": p["id"], "title": p.get("title", "Untitled"),
                    "created": p.get("createdDateTime", ""),
                    "updated": p.get("lastModifiedDateTime", ""),
                    "section_id": p.get("parentSection", {}).get("id", ""),
                }
                for p in resp.json().get("value", [])
            ]
        except Exception as e:
            logger.error("List pages failed: %s", e)
            return []

    async def get_page_content(self, page_id: str) -> Optional[str]:
        """Get page content as HTML."""
        try:
            resp = await self._http.get(
                f"{GRAPH_BASE}/me/onenote/pages/{page_id}/content",
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.error("Get page content failed: %s", e)
            return None

    async def create_page(self, section_id: str, title: str, body_html: str) -> dict:
        """Create a page in a section. Body is HTML."""
        html_content = f"""<!DOCTYPE html>
<html>
<head><title>{title}</title></head>
<body>
{body_html}
</body>
</html>"""
        resp = await self._http.post(
            f"{GRAPH_BASE}/me/onenote/sections/{section_id}/pages",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "text/html",
            },
            content=html_content.encode("utf-8"),
        )
        resp.raise_for_status()
        return resp.json()

    async def update_page(self, page_id: str, body_html: str) -> bool:
        """Append content to a page (OneNote PATCH append)."""
        try:
            resp = await self._http.patch(
                f"{GRAPH_BASE}/me/onenote/pages/{page_id}/content",
                headers=self._headers(),
                json=[{
                    "target": "body",
                    "action": "append",
                    "content": body_html,
                }],
            )
            return resp.status_code < 400
        except Exception:
            return False

    async def delete_page(self, page_id: str) -> bool:
        try:
            resp = await self._http.delete(
                f"{GRAPH_BASE}/me/onenote/pages/{page_id}",
                headers=self._headers(),
            )
            return resp.status_code < 400
        except Exception:
            return False

    # ── Search ────────────────────────────────────────────────────────

    async def search_pages(self, query: str, limit: int = 20) -> list[dict]:
        """Search OneNote pages by title (Graph API doesn't support full-text on content)."""
        try:
            resp = await self._http.get(
                f"{GRAPH_BASE}/me/onenote/pages",
                headers=self._headers(),
                params={
                    "$filter": f"contains(title, '{query}')",
                    "$top": str(limit),
                    "$select": "id,title,createdDateTime,lastModifiedDateTime",
                },
            )
            if resp.status_code == 200:
                return [
                    {"id": p["id"], "title": p.get("title", ""), "updated": p.get("lastModifiedDateTime", "")}
                    for p in resp.json().get("value", [])
                ]
            # Fallback: list all and filter client-side
            all_pages = await self.list_pages(limit=100)
            q = query.lower()
            return [p for p in all_pages if q in p.get("title", "").lower()][:limit]
        except Exception as e:
            logger.error("Search pages failed: %s", e)
            return []

    # ── Oak Notebook Management ───────────────────────────────────────

    async def ensure_oak_notebook(self) -> Optional[dict]:
        """Get or create the 'Oak' notebook and 'Notes' section."""
        notebooks = await self.list_notebooks()
        oak_nb = None
        for nb in notebooks:
            if nb["title"].lower() in ("oak", "oak-ai"):
                oak_nb = nb
                break
        if not oak_nb:
            try:
                result = await self.create_notebook("Oak")
                oak_nb = {"id": result["id"], "title": "Oak"}
            except Exception as e:
                logger.error("Failed to create Oak notebook: %s", e)
                return None

        # Ensure a "Notes" section exists
        sections = await self.list_sections(oak_nb["id"])
        notes_section = None
        for s in sections:
            if s["title"].lower() == "notes":
                notes_section = s
                break
        if not notes_section:
            try:
                result = await self.create_section(oak_nb["id"], "Notes")
                notes_section = {"id": result["id"], "title": "Notes"}
            except Exception:
                pass

        return {"notebook": oak_nb, "section": notes_section}

    async def save_ai_note(self, title: str, content_md: str, tags: list[str] = None) -> dict:
        """Save a note from the AI agent. Converts markdown to simple HTML."""
        oak = await self.ensure_oak_notebook()
        if not oak or not oak.get("section"):
            return {"error": "Could not find or create Oak notebook"}

        # Convert markdown to simple HTML
        body_html = self._md_to_html(content_md)
        if tags:
            body_html += f'<p style="color:gray;font-size:small">Tags: {", ".join(tags)}</p>'

        try:
            page = await self.create_page(oak["section"]["id"], title, body_html)
            return {"id": page.get("id", ""), "title": title, "status": "created"}
        except Exception as e:
            return {"error": str(e)}

    async def save_chat_summary(self, conversation_title: str, summary: str) -> dict:
        """Save a chat conversation summary as a OneNote page."""
        return await self.save_ai_note(
            f"Chat: {conversation_title}",
            summary,
            tags=["chat-summary", "ai-generated"],
        )

    # ── Wiki Sync ─────────────────────────────────────────────────────

    async def sync_page_to_wiki(self, page_id: str) -> Optional[dict]:
        """Import a OneNote page into the Oak wiki."""
        from backend.wiki_service import wiki_service
        content = await self.get_page_content(page_id)
        pages = await self.list_pages()
        page_meta = next((p for p in pages if p["id"] == page_id), None)
        if not content or not page_meta:
            return None
        # Strip HTML to get approximate markdown
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content, "html.parser")
        text = soup.get_text(separator="\n").strip()
        article = wiki_service.create_article(
            title=page_meta.get("title", "Imported"),
            content=text,
            tags=["onenote-import"],
        )
        return article

    async def sync_wiki_to_page(self, wiki_slug: str) -> Optional[dict]:
        """Export a wiki article to OneNote."""
        from backend.wiki_service import wiki_service
        article = wiki_service.get_article(wiki_slug)
        if not article:
            return None
        return await self.save_ai_note(
            article["title"],
            article["content"],
            tags=article.get("tags", []) + ["wiki-export"],
        )

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _md_to_html(md: str) -> str:
        """Simple markdown → HTML conversion for OneNote."""
        try:
            import markdown
            return markdown.markdown(md, extensions=["fenced_code", "tables"])
        except ImportError:
            # Fallback: wrap in <pre> for code-like content
            escaped = md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return f"<pre>{escaped}</pre>"


onenote_service = OneNoteService()
