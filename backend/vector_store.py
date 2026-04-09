"""
Vector store — ChromaDB-backed semantic search for wiki articles and repo code.
Uses sentence-transformers for local embeddings (no API calls).
"""
import logging
import hashlib
from typing import Optional
import chromadb
from chromadb.config import Settings as ChromaSettings
from backend.config import CHROMA_DIR, settings

logger = logging.getLogger("oak.vector")

WIKI_COLLECTION = "wiki"
CODE_COLLECTION = "code"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


class VectorStore:
    """Manages ChromaDB collections for wiki and code search."""

    def __init__(self):
        self._client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._wiki_col = self._client.get_or_create_collection(
            name=WIKI_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        self._code_col = self._client.get_or_create_collection(
            name=CODE_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

    # ── Chunking ─────────────────────────────────────────────────────

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
        """Split text into overlapping chunks."""
        if len(text) <= chunk_size:
            return [text]
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk)
            start += chunk_size - overlap
        return chunks

    @staticmethod
    def _doc_id(source: str, index: int) -> str:
        h = hashlib.md5(source.encode()).hexdigest()[:12]
        return f"{h}_{index}"

    # ── Wiki indexing ────────────────────────────────────────────────

    def index_wiki_article(self, slug: str, title: str, content: str):
        """Index a wiki article (replaces existing chunks for same slug)."""
        self._remove_by_source(self._wiki_col, slug)
        chunks = self._chunk_text(content)
        if not chunks:
            return
        ids = [self._doc_id(slug, i) for i in range(len(chunks))]
        metadatas = [{"source": slug, "title": title, "chunk": i} for i in range(len(chunks))]
        self._wiki_col.add(documents=chunks, ids=ids, metadatas=metadatas)
        logger.info("Indexed wiki '%s': %d chunks", slug, len(chunks))

    def remove_wiki_article(self, slug: str):
        """Remove all chunks for a wiki article."""
        self._remove_by_source(self._wiki_col, slug)

    def search_wiki(self, query: str, n_results: int = 5) -> list[dict]:
        """Semantic search across wiki articles."""
        if self._wiki_col.count() == 0:
            return []
        results = self._wiki_col.query(query_texts=[query], n_results=n_results)
        return self._format_results(results)

    # ── Code indexing ────────────────────────────────────────────────

    def index_code_file(self, repo_name: str, filepath: str, content: str):
        """Index a code file from a repo."""
        source = f"{repo_name}/{filepath}"
        self._remove_by_source(self._code_col, source)
        chunks = self._chunk_text(content, chunk_size=1500, overlap=300)
        if not chunks:
            return
        ids = [self._doc_id(source, i) for i in range(len(chunks))]
        metadatas = [
            {"source": source, "repo": repo_name, "file": filepath, "chunk": i}
            for i in range(len(chunks))
        ]
        self._code_col.add(documents=chunks, ids=ids, metadatas=metadatas)

    def index_repo(self, repo_name: str, files: dict[str, str]) -> int:
        """Index multiple files from a repo. Returns count of indexed files."""
        count = 0
        for filepath, content in files.items():
            if content and len(content.strip()) > 20:
                self.index_code_file(repo_name, filepath, content)
                count += 1
        logger.info("Indexed repo '%s': %d files", repo_name, count)
        return count

    def remove_repo(self, repo_name: str):
        """Remove all indexed files for a repo."""
        try:
            existing = self._code_col.get(where={"repo": repo_name})
            if existing["ids"]:
                self._code_col.delete(ids=existing["ids"])
        except Exception:
            pass

    def search_code(self, query: str, repo_name: Optional[str] = None, n_results: int = 5) -> list[dict]:
        """Semantic search across indexed code."""
        if self._code_col.count() == 0:
            return []
        where = {"repo": repo_name} if repo_name else None
        results = self._code_col.query(query_texts=[query], n_results=n_results, where=where)
        return self._format_results(results)

    # ── Combined search (RAG) ────────────────────────────────────────

    def search_all(self, query: str, n_results: int = 5) -> list[str]:
        """Search both wiki and code, return combined context strings for RAG."""
        wiki_hits = self.search_wiki(query, n_results=n_results)
        code_hits = self.search_code(query, n_results=n_results)

        context = []
        for hit in wiki_hits:
            context.append(f"[Wiki: {hit.get('title', hit['source'])}]\n{hit['text']}")
        for hit in code_hits:
            context.append(f"[Code: {hit['source']}]\n{hit['text']}")

        return context[:n_results]

    # ── Stats ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "wiki_chunks": self._wiki_col.count(),
            "code_chunks": self._code_col.count(),
        }

    # ── Helpers ──────────────────────────────────────────────────────

    def _remove_by_source(self, collection, source: str):
        try:
            existing = collection.get(where={"source": source})
            if existing["ids"]:
                collection.delete(ids=existing["ids"])
        except Exception:
            pass

    @staticmethod
    def _format_results(results: dict) -> list[dict]:
        if not results or not results.get("documents"):
            return []
        formatted = []
        docs = results["documents"][0]
        metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
        dists = results["distances"][0] if results.get("distances") else [0] * len(docs)
        for doc, meta, dist in zip(docs, metas, dists):
            formatted.append({
                "text": doc,
                "source": meta.get("source", ""),
                "title": meta.get("title", ""),
                "score": round(1 - dist, 4),
                **meta,
            })
        return formatted


vector_store = VectorStore()
