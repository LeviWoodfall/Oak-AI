"""
Wiki knowledge base — markdown-based local wiki with vector search.
Articles stored as markdown files with YAML frontmatter.
"""
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import frontmatter
import markdown
from backend.config import WIKI_DIR
from backend.vector_store import vector_store

logger = logging.getLogger("oak.wiki")


def _slugify(text: str) -> str:
    """Convert title to a filesystem-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", slug).strip("-")[:80]


class WikiService:
    """Local markdown wiki with semantic search integration."""

    def __init__(self):
        WIKI_DIR.mkdir(parents=True, exist_ok=True)

    # ── CRUD ─────────────────────────────────────────────────────────

    def create_article(self, title: str, content: str, tags: Optional[list[str]] = None) -> dict:
        """Create a new wiki article."""
        slug = _slugify(title)
        filepath = WIKI_DIR / f"{slug}.md"

        if filepath.exists():
            slug = f"{slug}-{int(time.time())}"
            filepath = WIKI_DIR / f"{slug}.md"

        post = frontmatter.Post(
            content,
            title=title,
            slug=slug,
            tags=tags or [],
            created=datetime.now(timezone.utc).isoformat(),
            updated=datetime.now(timezone.utc).isoformat(),
        )
        filepath.write_text(frontmatter.dumps(post), encoding="utf-8")

        # Index into vector store
        vector_store.index_wiki_article(slug, title, content)
        logger.info("Created wiki article: %s", slug)

        return self._article_to_dict(post, slug)

    def get_article(self, slug: str) -> Optional[dict]:
        """Get a single article by slug."""
        filepath = WIKI_DIR / f"{slug}.md"
        if not filepath.exists():
            return None
        post = frontmatter.load(str(filepath))
        return self._article_to_dict(post, slug)

    def update_article(self, slug: str, title: Optional[str] = None,
                       content: Optional[str] = None, tags: Optional[list[str]] = None) -> Optional[dict]:
        """Update an existing article."""
        filepath = WIKI_DIR / f"{slug}.md"
        if not filepath.exists():
            return None

        post = frontmatter.load(str(filepath))
        if title is not None:
            post["title"] = title
        if content is not None:
            post.content = content
        if tags is not None:
            post["tags"] = tags
        post["updated"] = datetime.now(timezone.utc).isoformat()

        filepath.write_text(frontmatter.dumps(post), encoding="utf-8")

        # Re-index
        vector_store.index_wiki_article(slug, post["title"], post.content)
        return self._article_to_dict(post, slug)

    def delete_article(self, slug: str) -> bool:
        """Delete an article."""
        filepath = WIKI_DIR / f"{slug}.md"
        if not filepath.exists():
            return False
        filepath.unlink()
        vector_store.remove_wiki_article(slug)
        logger.info("Deleted wiki article: %s", slug)
        return True

    # ── Listing & Search ─────────────────────────────────────────────

    def list_articles(self, tag: Optional[str] = None) -> list[dict]:
        """List all articles, optionally filtered by tag."""
        articles = []
        for f in sorted(WIKI_DIR.glob("*.md")):
            try:
                post = frontmatter.load(str(f))
                slug = f.stem
                if tag and tag not in post.get("tags", []):
                    continue
                articles.append(self._article_to_dict(post, slug, include_content=False))
            except Exception as e:
                logger.warning("Error reading %s: %s", f.name, e)
        return articles

    def search(self, query: str, n_results: int = 10) -> list[dict]:
        """Semantic search across wiki articles."""
        return vector_store.search_wiki(query, n_results=n_results)

    def get_all_tags(self) -> list[str]:
        """Get all unique tags across articles."""
        tags = set()
        for f in WIKI_DIR.glob("*.md"):
            try:
                post = frontmatter.load(str(f))
                tags.update(post.get("tags", []))
            except Exception:
                pass
        return sorted(tags)

    # ── Render ───────────────────────────────────────────────────────

    def render_html(self, slug: str) -> Optional[str]:
        """Render article content as HTML."""
        article = self.get_article(slug)
        if not article:
            return None
        return markdown.markdown(
            article["content"],
            extensions=["fenced_code", "tables", "codehilite", "toc"],
        )

    # ── Bulk re-index ────────────────────────────────────────────────

    def reindex_all(self) -> int:
        """Re-index all wiki articles into the vector store."""
        count = 0
        for f in WIKI_DIR.glob("*.md"):
            try:
                post = frontmatter.load(str(f))
                slug = f.stem
                vector_store.index_wiki_article(slug, post.get("title", slug), post.content)
                count += 1
            except Exception as e:
                logger.warning("Failed to index %s: %s", f.name, e)
        logger.info("Re-indexed %d wiki articles", count)
        return count

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _article_to_dict(post, slug: str, include_content: bool = True) -> dict:
        d = {
            "slug": slug,
            "title": post.get("title", slug),
            "tags": post.get("tags", []),
            "created": post.get("created", ""),
            "updated": post.get("updated", ""),
        }
        if include_content:
            d["content"] = post.content
        return d


wiki_service = WikiService()
