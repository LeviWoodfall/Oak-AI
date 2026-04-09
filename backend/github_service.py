"""
GitHub integration — clone repos, browse files, index into knowledge base.
"""
import logging
import os
import shutil
from pathlib import Path
from typing import Optional
from git import Repo as GitRepo, InvalidGitRepositoryError
from github import Github, GithubException
from backend.config import settings, REPOS_DIR

logger = logging.getLogger("oak.github")


class GitHubService:
    """Manages GitHub repo cloning, browsing, and file access."""

    def __init__(self):
        self._gh: Optional[Github] = None
        if settings.github_token:
            self._gh = Github(settings.github_token)

    @property
    def authenticated(self) -> bool:
        return self._gh is not None

    def set_token(self, token: str):
        """Set or update the GitHub token at runtime."""
        self._gh = Github(token)
        os.environ["GITHUB_TOKEN"] = token

    # ── Repo listing ─────────────────────────────────────────────────

    def list_remote_repos(self, query: Optional[str] = None, limit: int = 20) -> list[dict]:
        """List user's repos or search public repos."""
        if not self._gh:
            return []
        try:
            if query:
                repos = self._gh.search_repositories(query, sort="stars")
            else:
                repos = self._gh.get_user().get_repos(sort="updated")
            results = []
            for repo in repos[:limit]:
                results.append({
                    "full_name": repo.full_name,
                    "description": repo.description or "",
                    "language": repo.language or "Unknown",
                    "stars": repo.stargazers_count,
                    "url": repo.html_url,
                    "clone_url": repo.clone_url,
                    "private": repo.private,
                })
            return results
        except GithubException as e:
            logger.error("GitHub API error: %s", e)
            return []

    def list_local_repos(self) -> list[dict]:
        """List cloned repos in the repos directory."""
        repos = []
        if not REPOS_DIR.exists():
            return repos
        for d in sorted(REPOS_DIR.iterdir()):
            if d.is_dir() and (d / ".git").exists():
                try:
                    gr = GitRepo(d)
                    repos.append({
                        "name": d.name,
                        "path": str(d),
                        "branch": gr.active_branch.name if not gr.head.is_detached else "detached",
                        "last_commit": gr.head.commit.message.strip()[:80],
                    })
                except (InvalidGitRepositoryError, Exception) as e:
                    logger.warning("Skipping %s: %s", d.name, e)
        return repos

    # ── Clone / Pull ─────────────────────────────────────────────────

    def clone_repo(self, url: str, name: Optional[str] = None) -> dict:
        """Clone a repo into the repos directory."""
        if name is None:
            name = url.rstrip("/").split("/")[-1].replace(".git", "")
        dest = REPOS_DIR / name
        if dest.exists():
            return {"status": "exists", "path": str(dest), "name": name}

        clone_url = url
        if settings.github_token and "github.com" in url:
            clone_url = url.replace("https://", f"https://{settings.github_token}@")

        try:
            GitRepo.clone_from(clone_url, str(dest))
            logger.info("Cloned %s to %s", url, dest)
            return {"status": "cloned", "path": str(dest), "name": name}
        except Exception as e:
            logger.error("Clone failed: %s", e)
            return {"status": "error", "error": str(e)}

    def pull_repo(self, name: str) -> dict:
        """Pull latest changes for a cloned repo."""
        dest = REPOS_DIR / name
        if not dest.exists():
            return {"status": "not_found"}
        try:
            gr = GitRepo(dest)
            origin = gr.remotes.origin
            origin.pull()
            return {"status": "pulled", "branch": gr.active_branch.name}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def delete_repo(self, name: str) -> dict:
        """Remove a cloned repo."""
        dest = REPOS_DIR / name
        if not dest.exists():
            return {"status": "not_found"}
        shutil.rmtree(dest)
        return {"status": "deleted"}

    # ── File browsing ────────────────────────────────────────────────

    def browse_repo(self, name: str, subpath: str = "") -> list[dict]:
        """List files/dirs in a cloned repo."""
        base = REPOS_DIR / name
        target = base / subpath if subpath else base
        if not target.exists() or not str(target.resolve()).startswith(str(base.resolve())):
            return []

        items = []
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name.startswith("."):
                continue
            rel = str(entry.relative_to(base))
            items.append({
                "name": entry.name,
                "path": rel,
                "type": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
            })
        return items

    def read_file(self, name: str, filepath: str) -> Optional[str]:
        """Read a file from a cloned repo. Returns None if not found."""
        base = REPOS_DIR / name
        target = base / filepath
        resolved = target.resolve()
        if not resolved.is_file() or not str(resolved).startswith(str(base.resolve())):
            return None
        try:
            return resolved.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

    def get_python_files(self, name: str) -> list[str]:
        """Get all Python file paths in a repo (for indexing)."""
        base = REPOS_DIR / name
        if not base.exists():
            return []
        return [
            str(p.relative_to(base))
            for p in base.rglob("*.py")
            if ".git" not in p.parts and "__pycache__" not in p.parts
        ]


github_service = GitHubService()
