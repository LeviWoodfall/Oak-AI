"""
Autonomous Learning Engine — Oak's growth engine.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import httpx
from backend.config import DATA_DIR, settings
from backend.agent.self_improver import skill_extractor

# Cross-platform file locking
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

try:
    import msvcrt
    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False

logger = logging.getLogger("oak.learner")

LEARNER_DIR = DATA_DIR / "learner"
LEARNER_DIR.mkdir(parents=True, exist_ok=True)
PROCESS_LOG = LEARNER_DIR / "process_log.json"
LEARNING_LOG = LEARNER_DIR / "learning_log.jsonl"
DAILY_REPORT = LEARNER_DIR / "daily_reports"
DAILY_REPORT.mkdir(parents=True, exist_ok=True)

MAX_PROCESS_COUNT = 5  # Process each repo up to 5 times for comprehensive learning


class FileLock:
    """Cross-platform file lock context manager."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.lock_file = None
        self._locked = False

    def __enter__(self):
        # Create a lock file
        self.lock_file = self.file_path.with_suffix(".lock")
        try:
            # Try to create/open the lock file exclusively
            fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            self._locked = True
            return fd
        except FileExistsError:
            # Lock file exists, try to wait for it
            for _ in range(30):  # Wait up to 30 seconds
                try:
                    fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                    self._locked = True
                    return fd
                except FileExistsError:
                    time.sleep(1)
            raise RuntimeError(f"Could not acquire lock on {self.file_path}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._locked and self.lock_file and self.lock_file.exists():
            try:
                self.lock_file.unlink()
            except Exception:
                pass


class RepoTracker:
    """Tracks how many times each repo has been processed."""

    def __init__(self):
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self):
        with FileLock(PROCESS_LOG):
            if PROCESS_LOG.exists():
                try:
                    self._data = json.loads(PROCESS_LOG.read_text(encoding="utf-8"))
                except Exception:
                    self._data = {}

    def _save(self):
        with FileLock(PROCESS_LOG):
            PROCESS_LOG.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")

    def get_count(self, repo_url: str) -> int:
        key = self._key(repo_url)
        return self._data.get(key, {}).get("count", 0)

    def should_process(self, repo_url: str, current_commit: str = "") -> bool:
        """Check if repo should be processed (either not maxed out or has updates)."""
        count = self.get_count(repo_url)
        if count < MAX_PROCESS_COUNT:
            return True
        # Even if maxed out, re-process if there are updates
        if current_commit and self.needs_update(repo_url, current_commit):
            return True
        return False

    def reset_for_update(self, repo_url: str):
        """Reset processing count when repo has been updated."""
        key = self._key(repo_url)
        if key in self._data:
            self._data[key]["count"] = 0
            self._save()

    def record_processing(self, repo_url: str, success: bool, articles_created: int = 0,
                          facts_extracted: int = 0, last_commit: str = ""):
        key = self._key(repo_url)
        entry = self._data.get(key, {"count": 0, "url": repo_url, "history": []})
        entry["count"] = entry.get("count", 0) + 1
        entry["last_processed"] = datetime.now(timezone.utc).isoformat()
        entry["last_commit_seen"] = last_commit
        entry["history"].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": success,
            "articles_created": articles_created,
            "facts_extracted": facts_extracted,
            "pass_number": entry["count"],
            "commit": last_commit,
        })
        self._data[key] = entry
        self._save()

    def needs_update(self, repo_url: str, current_commit: str) -> bool:
        """Check if repo has been updated since last processing."""
        key = self._key(repo_url)
        entry = self._data.get(key, {})
        last_seen = entry.get("last_commit_seen", "")
        return last_seen != current_commit

    def get_all(self) -> dict:
        return self._data

    def stats(self) -> dict:
        total = len(self._data)
        completed = sum(1 for v in self._data.values() if v.get("count", 0) >= MAX_PROCESS_COUNT)
        in_progress = total - completed
        return {"total_repos_seen": total, "completed_5x": completed, "in_progress": in_progress}

    @staticmethod
    def _key(url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()[:16]


class AutoLearner:
    """Autonomous knowledge acquisition engine."""

    def __init__(self):
        self.tracker = RepoTracker()
        self._http = httpx.AsyncClient(timeout=30, follow_redirects=True)
        self._running = False
        self._last_run = ""
        self._last_report: dict = {}

    # ── Repo Cloning and Local Analysis ───────────────────────────────

    def _get_clone_dir(self, repo_name: str) -> Path:
        """Get the local directory for a cloned repo."""
        safe_name = repo_name.replace("/", "_").replace("\\", "_")
        return LEARNER_DIR / "repos" / safe_name

    async def _clone_repo(self, repo_name: str) -> Path:
        """Clone a GitHub repository locally for comprehensive analysis."""
        clone_dir = self._get_clone_dir(repo_name)

        # Remove existing clone if it exists
        if clone_dir.exists():
            shutil.rmtree(clone_dir)

        clone_dir.parent.mkdir(parents=True, exist_ok=True)

        clone_url = f"https://github.com/{repo_name}.git"
        logger.info("Cloning %s to %s", repo_name, clone_dir)

        # Create a temporary git config for credentials (avoids token exposure in process list)
        git_config_dir = clone_dir.parent / ".git_config_temp"
        git_config_dir.mkdir(parents=True, exist_ok=True)
        git_config_file = git_config_dir / "config"

        try:
            # Set up credential helper if token is available
            if settings.github_token:
                with open(git_config_file, "w") as f:
                    f.write(f'[credential "https://github.com"]\n')
                    f.write(f'    helper = !echo "username={settings.github_token}&password="\n')

            # Run git clone with temporary config
            cmd = ["git", "clone", "--depth", "1", clone_url, str(clone_dir)]
            env = os.environ.copy()
            if settings.github_token:
                env["GIT_CONFIG"] = str(git_config_file)

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                env=env,
            )
            if result.returncode != 0:
                logger.error("Git clone failed for %s: %s", repo_name, result.stderr)
                raise RuntimeError(f"Git clone failed: {result.stderr}")
            logger.info("Successfully cloned %s", repo_name)
            return clone_dir
        except subprocess.TimeoutExpired:
            logger.error("Git clone timed out for %s", repo_name)
            raise
        except Exception as e:
            logger.error("Failed to clone %s: %s", repo_name, e)
            raise
        finally:
            # Clean up temporary config
            if git_config_file.exists():
                try:
                    git_config_file.unlink()
                except Exception:
                    pass
            try:
                git_config_dir.rmdir()
            except Exception:
                pass

    def _cleanup_repo(self, repo_name: str):
        """Remove the cloned repository after processing with retries."""
        clone_dir = self._get_clone_dir(repo_name)
        if not clone_dir.exists():
            return

        for attempt in range(3):
            try:
                shutil.rmtree(clone_dir)
                logger.info("Cleaned up clone of %s", repo_name)
                return
            except PermissionError:
                logger.warning("Cleanup attempt %d failed for %s: permission denied, retrying...", attempt + 1, repo_name)
                time.sleep(1)
            except Exception as e:
                logger.warning("Cleanup attempt %d failed for %s: %s", attempt + 1, repo_name, e)
                time.sleep(1)

        # Final attempt with more forceful cleanup on Windows
        try:
            if os.name == 'nt':
                subprocess.run(['rd', '/s', '/q', str(clone_dir)], shell=True, capture_output=True)
            else:
                subprocess.run(['rm', '-rf', str(clone_dir)], capture_output=True)
            logger.info("Force cleaned up clone of %s", repo_name)
        except Exception as e:
            logger.error("Failed to cleanup %s after all attempts: %s", repo_name, e)

    def _walk_repo_files(self, clone_dir: Path, max_files: int = 500) -> dict[str, Path]:
        """Walk through all files in the cloned repository."""
        files = {}
        file_count = 0

        # File extensions to include (source code, configs, docs)
        include_extensions = {
            '.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.go', '.rs', '.c', '.cpp', '.h',
            '.hpp', '.cs', '.php', '.rb', '.swift', '.kt', '.scala', '.dart', '.lua',
            '.json', '.yaml', '.yml', '.toml', '.xml', '.ini', '.cfg', '.conf',
            '.md', '.rst', '.txt', '.sh', '.bat', '.ps1', '.dockerfile',
            '.sql', '.graphql', '.proto', '.thrift'
        }

        # Directories to skip
        skip_dirs = {
            '.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env',
            'dist', 'build', 'target', 'bin', 'obj', '.next', '.vscode',
            '.idea', 'coverage', '.pytest_cache', '.mypy_cache'
        }

        try:
            for root, dirs, filenames in os.walk(clone_dir):
                # Skip unwanted directories
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]

                for filename in filenames:
                    # Check file extension
                    ext = Path(filename).suffix.lower()
                    if ext in include_extensions:
                        rel_path = Path(root).relative_to(clone_dir) / filename
                        files[str(rel_path)] = Path(root) / filename
                        file_count += 1

                        if file_count >= max_files:
                            logger.info("Reached max file limit (%d) for repo", max_files)
                            return files
        except Exception as e:
            logger.error("Error walking repo files: %s", e)

        logger.info("Found %d files in repo", len(files))
        return files

    def _read_file_content(self, file_path: Path, max_size: int = 50000) -> str:
        """Read file content with size limit."""
        try:
            if file_path.stat().st_size > max_size:
                return f"[File too large: {file_path.stat().st_size} bytes]"
            return file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.debug("Failed to read %s: %s", file_path, e)
            return f"[Error reading file: {e}]"

    # ── Discovery: Find top repos from multiple sources ───────────────

    async def _fetch_latest_commit(self, repo_name: str) -> str:
        """Fetch the latest commit SHA from GitHub API."""
        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.github_token:
            headers["Authorization"] = f"token {settings.github_token}"
        try:
            resp = await self._http.get(
                f"https://api.github.com/repos/{repo_name}/commits",
                headers=headers,
                params={"per_page": "1"},
            )
            if resp.status_code == 200:
                commits = resp.json()
                if commits and len(commits) > 0:
                    return commits[0].get("sha", "")
        except Exception as e:
            logger.debug("Failed to fetch commit for %s: %s", repo_name, e)
        return ""

    async def discover_trending_repos(self, limit: int = 20) -> list[dict]:
        """Discover top trending repos from GitHub and other sources."""
        repos = []

        # Source 1: GitHub Search API — most starred repos created/pushed recently
        try:
            github_repos = await self._github_search_trending(limit=limit)
            repos.extend(github_repos)
            logger.info("GitHub search: found %d repos", len(github_repos))
        except Exception as e:
            logger.warning("GitHub search failed: %s", e)

        # Source 2: GitHub trending page scrape (backup)
        if len(repos) < limit:
            try:
                trending = await self._github_trending_scrape(limit=limit - len(repos))
                repos.extend(trending)
                logger.info("GitHub trending scrape: found %d repos", len(trending))
            except Exception as e:
                logger.warning("GitHub trending scrape failed: %s", e)

        # Source 3: GitHub topics — popular topics with high-quality repos
        if len(repos) < limit:
            try:
                topic_repos = await self._github_topic_repos(limit=limit - len(repos))
                repos.extend(topic_repos)
            except Exception as e:
                logger.warning("GitHub topics failed: %s", e)

        # Deduplicate by URL
        seen = set()
        unique = []
        for r in repos:
            if r["url"] not in seen:
                seen.add(r["url"])
                unique.append(r)
        return unique[:limit]

    async def _github_search_trending(self, limit: int = 20) -> list[dict]:
        """Use GitHub Search API to find recently popular repos."""
        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.github_token:
            headers["Authorization"] = f"token {settings.github_token}"

        # Search for repos with many stars, pushed in last 7 days
        from datetime import timedelta
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

        resp = await self._http.get(
            "https://api.github.com/search/repositories",
            headers=headers,
            params={
                "q": f"pushed:>{week_ago} stars:>100",
                "sort": "stars",
                "order": "desc",
                "per_page": str(limit),
            },
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [
            {
                "url": item["html_url"],
                "name": item["full_name"],
                "description": item.get("description", "")[:200],
                "stars": item.get("stargazers_count", 0),
                "language": item.get("language", ""),
                "topics": item.get("topics", [])[:5],
                "source": "github_search",
                "commit": item.get("pushed_at", ""),  # Use pushed_at as proxy for commit
            }
            for item in items
        ]

    async def _github_trending_scrape(self, limit: int = 10) -> list[dict]:
        """Scrape GitHub trending page as backup source."""
        try:
            resp = await self._http.get("https://github.com/trending")
            if resp.status_code != 200:
                return []
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            repos = []
            for article in soup.select("article.Box-row")[:limit]:
                h2 = article.select_one("h2 a")
                if not h2:
                    continue
                href = h2.get("href", "").strip()
                name = href.lstrip("/")
                desc_el = article.select_one("p")
                desc = desc_el.get_text(strip=True) if desc_el else ""
                repos.append({
                    "url": f"https://github.com{href}",
                    "name": name,
                    "description": desc[:200],
                    "stars": 0,
                    "language": "",
                    "topics": [],
                    "source": "github_trending",
                })
            return repos
        except Exception:
            return []

    async def _github_topic_repos(self, limit: int = 5) -> list[dict]:
        """Get repos from popular GitHub topics."""
        topics = ["machine-learning", "python", "typescript", "rust", "llm", "ai-agents"]
        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.github_token:
            headers["Authorization"] = f"token {settings.github_token}"

        repos = []
        for topic in topics[:3]:
            try:
                resp = await self._http.get(
                    "https://api.github.com/search/repositories",
                    headers=headers,
                    params={"q": f"topic:{topic} stars:>500", "sort": "updated",
                            "per_page": "3"},
                )
                if resp.status_code == 200:
                    for item in resp.json().get("items", []):
                        repos.append({
                            "url": item["html_url"],
                            "name": item["full_name"],
                            "description": item.get("description", "")[:200],
                            "stars": item.get("stargazers_count", 0),
                            "language": item.get("language", ""),
                            "topics": item.get("topics", [])[:5],
                            "source": f"topic:{topic}",
                        })
                await asyncio.sleep(1)  # Rate limit respect
            except Exception:
                pass
        return repos[:limit]

    # ── Processing: Analyze repos and extract knowledge ───────────────

    async def process_repo(self, repo: dict) -> dict:
        """Analyze a single repo by cloning it locally for comprehensive analysis."""
        url = repo["url"]
        name = repo["name"]
        result = {"repo": name, "url": url, "articles_created": 0,
                  "facts_extracted": 0, "success": False, "pass_number": 0,
                  "files_analyzed": 0}

        # Validate repo name format
        if not name or "/" not in name:
            result["skipped"] = True
            result["reason"] = f"Invalid repo name format: {name}"
            logger.warning("Skipping invalid repo name: %s", name)
            return result

        # Fetch latest commit to check for updates
        latest_commit = await self._fetch_latest_commit(name)
        if not latest_commit:
            latest_commit = repo.get("commit", "")

        # Check if we should process (not maxed out or has updates)
        if not self.tracker.should_process(url, latest_commit):
            result["skipped"] = True
            result["reason"] = f"Already processed {MAX_PROCESS_COUNT} times and no updates"
            return result

        # Reset count if repo has been updated
        if self.tracker.needs_update(url, latest_commit):
            logger.info("Repo %s has updates, resetting processing count", name)
            self.tracker.reset_for_update(url)

        pass_number = self.tracker.get_count(url) + 1
        result["pass_number"] = pass_number
        logger.info("Processing repo %s (pass %d/%d)", name, pass_number, MAX_PROCESS_COUNT)

        clone_dir = None
        try:
            # Clone the repository locally
            clone_dir = await self._clone_repo(name)

            # Walk through all files in the repo
            all_files = self._walk_repo_files(clone_dir, max_files=500)
            result["files_analyzed"] = len(all_files)

            # Read file contents (with limits per pass)
            key_files = {}
            files_per_pass = 100  # Process up to 100 files per pass

            # Distribute files across passes
            file_list = list(all_files.items())
            start_idx = (pass_number - 1) * files_per_pass
            end_idx = min(start_idx + files_per_pass, len(file_list))

            for rel_path, file_path in file_list[start_idx:end_idx]:
                content = self._read_file_content(file_path)
                key_files[rel_path] = content

            logger.info("Pass %d: analyzing %d files (indices %d-%d)",
                       pass_number, len(key_files), start_idx, end_idx)

            # Build structure representation
            structure = [f"📁 {p}" if Path(p).parent != Path(".") else f"📄 {Path(p).name}"
                        for p in all_files.keys()][:50]

            # Fetch README if present in clone (check exact filename match)
            readme_content = ""
            readme_patterns = ["README.md", "README.rst", "README.txt", "readme.md", "readme.rst", "readme.txt"]
            for rel_path, file_path in all_files.items():
                filename = Path(rel_path).name
                if filename in readme_patterns:
                    readme_content = self._read_file_content(file_path)
                    logger.info("Found README: %s", rel_path)
                    break

            # Build knowledge from comprehensive file analysis
            knowledge = self._extract_knowledge(repo, readme_content, structure, key_files,
                                                 pass_number)

            # Create wiki article
            articles = await self._create_wiki_articles(name, knowledge, pass_number)
            result["articles_created"] = len(articles)

            # Ingest into tiered context
            facts = await self._ingest_context(name, knowledge, pass_number)
            result["facts_extracted"] = facts

            # Store in memory
            self._store_learning(name, knowledge, pass_number)

            # Extract skills from this repository
            try:
                skills = skill_extractor.extract_from_knowledge(name, knowledge)
                if skills:
                    result["skills_learned"] = len(skills)
                    logger.info("Extracted %d skills from %s", len(skills), name)

                    # Feed into unified skill library (Memento pattern)
                    from backend.agent.skill_library import skill_library
                    for skill in skills:
                        skill_library.add_skill(
                            name=skill.name,
                            description=skill.description,
                            content=skill.code_example,
                            category=skill.category,
                            source="learned",
                            tags=skill.tags,
                            source_repo=name,
                        )
            except Exception as e:
                logger.warning("Failed to extract skills from %s: %s", name, e)

            result["success"] = True
            self.tracker.record_processing(url, True, len(articles), facts, latest_commit)

        except Exception as e:
            logger.error("Failed to process %s: %s", name, e)
            result["error"] = str(e)
            self.tracker.record_processing(url, False, 0, 0, latest_commit)
        finally:
            # Always cleanup the cloned repo
            if clone_dir:
                self._cleanup_repo(name)

        return result

    async def _fetch_readme(self, repo_name: str) -> str:
        """Fetch README from GitHub API."""
        headers = {"Accept": "application/vnd.github.v3.raw"}
        if settings.github_token:
            headers["Authorization"] = f"token {settings.github_token}"
        try:
            resp = await self._http.get(
                f"https://api.github.com/repos/{repo_name}/readme",
                headers=headers,
            )
            if resp.status_code == 200:
                return resp.text[:10000]  # Cap at 10k chars
            elif resp.status_code == 404:
                logger.debug("No README found for %s", repo_name)
            else:
                logger.warning("Failed to fetch README for %s: %s", repo_name, resp.status_code)
        except Exception as e:
            logger.warning("Error fetching README for %s: %s", repo_name, e)
        return ""

    async def _fetch_structure(self, repo_name: str) -> list[str]:
        """Fetch top-level directory structure."""
        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.github_token:
            headers["Authorization"] = f"token {settings.github_token}"
        try:
            resp = await self._http.get(
                f"https://api.github.com/repos/{repo_name}/contents/",
                headers=headers,
            )
            if resp.status_code == 200:
                items = resp.json()
                return [f"{'📁' if i['type'] == 'dir' else '📄'} {i['name']}"
                        for i in items[:50]]
            elif resp.status_code == 404:
                logger.debug("Could not fetch structure for %s (repo may be empty)", repo_name)
            else:
                logger.warning("Failed to fetch structure for %s: %s", repo_name, resp.status_code)
        except Exception as e:
            logger.warning("Error fetching structure for %s: %s", repo_name, e)
        return []

    def _determine_targets(self, pass_number: int, language: str,
                          available_files: list[str]) -> list[str]:
        """Determine which files to fetch based on pass, language, and available files."""
        targets = []

        # Normalize language for matching
        lang_lower = language.lower() if language else ""

        if pass_number == 1:
            # Config files based on language
            lang_configs = {
                "python": ["requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "poetry.lock", "environment.yml"],
                "typescript": ["package.json", "tsconfig.json", "yarn.lock", "package-lock.json", "tsconfig.base.json"],
                "javascript": ["package.json", "yarn.lock", "package-lock.json"],
                "rust": ["Cargo.toml", "Cargo.lock"],
                "go": ["go.mod", "go.sum", "go.work"],
                "java": ["pom.xml", "build.gradle", "build.gradle.kts", "gradle.properties"],
                "c++": ["CMakeLists.txt", "Makefile", "configure.ac"],
                "ruby": ["Gemfile", "Gemfile.lock", "Rakefile"],
                "php": ["composer.json", "composer.lock"],
            }

            # Match language (case-insensitive, partial match)
            for lang, configs in lang_configs.items():
                if lang in lang_lower or lang_lower in lang:
                    targets.extend(configs)
                    break

            # Common config files
            targets.extend(["LICENSE", "LICENSE.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md", ".gitignore", ".dockerignore", "Dockerfile", "docker-compose.yml"])

        elif pass_number == 2:
            # Source files - look for main entry points and core source files
            source_patterns = {
                "python": ["main.py", "app.py", "__init__.py", "src/__init__.py", "manage.py", "wsgi.py", "asgi.py"],
                "typescript": ["index.ts", "main.ts", "src/index.ts", "src/main.ts", "app.ts", "server.ts"],
                "javascript": ["index.js", "main.js", "src/index.js", "src/main.js", "app.js", "server.js"],
                "rust": ["main.rs", "lib.rs", "src/main.rs", "src/lib.rs"],
                "go": ["main.go", "cmd/main.go", "server.go"],
            }

            for lang, patterns in source_patterns.items():
                if lang in lang_lower or lang_lower in lang:
                    targets.extend(patterns)
                    break

            # Also look for common source directories and all files in them
            for f in available_files:
                if f in ["src/", "lib/", "app/", "server/", "api/", "core/"]:
                    targets.append(f)

        elif pass_number == 3:
            # Documentation and architecture
            targets.extend(["README.md", "CHANGELOG.md", "ARCHITECTURE.md", "docs/README.md", "docs/ARCHITECTURE.md", "docs/API.md"])
            for f in available_files:
                if f.startswith("test_") or f.endswith(".test.ts") or f.endswith(".test.js") or f.endswith("_test.py"):
                    targets.append(f)
                if f.startswith("tests/") or f.startswith("test/") or f.startswith("__tests__/"):
                    targets.append(f)

        elif pass_number == 4:
            # Additional source files and utilities
            for f in available_files:
                # Add utility files
                if "util" in f.lower() or "helper" in f.lower() or "utils" in f.lower():
                    targets.append(f)
                # Add config files
                if f.endswith(".config.js") or f.endswith(".config.ts") or f.endswith(".rc"):
                    targets.append(f)
                # Add middleware files
                if "middleware" in f.lower():
                    targets.append(f)

        elif pass_number == 5:
            # Example files, templates, and additional docs
            for f in available_files:
                if f.startswith("example") or f.startswith("sample") or f.startswith("demo"):
                    targets.append(f)
                if f.endswith(".example") or f.endswith(".template") or f.endswith(".sample"):
                    targets.append(f)
                if f.startswith("templates/") or f.startswith("template/"):
                    targets.append(f)

        # Filter to only files that actually exist
        return [f for f in targets if f in available_files or any(f.startswith(av.rstrip("/")) for av in available_files)]

    def _extract_knowledge(self, repo: dict, readme: str, structure: list[str],
                           key_files: dict, pass_number: int) -> dict:
        """Extract structured knowledge from repo data with comprehensive code analysis."""
        knowledge = {
            "name": repo["name"],
            "description": repo.get("description", ""),
            "language": repo.get("language", ""),
            "stars": repo.get("stars", 0),
            "topics": repo.get("topics", []),
            "structure_summary": "\n".join(structure[:20]),
            "readme_summary": readme[:3000],
            "pass_number": pass_number,
            "key_files": {k: v[:2000] for k, v in key_files.items()},
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }

        # Extract patterns from README
        knowledge["technologies"] = self._extract_technologies(readme)
        knowledge["key_concepts"] = self._extract_concepts(readme)

        # Analyze code structure from all files
        all_code = "\n".join(key_files.values())
        knowledge["code_technologies"] = self._extract_technologies(all_code)
        knowledge["code_patterns"] = self._extract_code_patterns(all_code)

        # Extract file type distribution
        file_types = {}
        for path in key_files.keys():
            ext = Path(path).suffix.lower()
            file_types[ext] = file_types.get(ext, 0) + 1
        knowledge["file_type_distribution"] = file_types

        return knowledge

    @staticmethod
    def _extract_code_patterns(text: str) -> list[str]:
        """Extract code patterns and architectural insights."""
        patterns = []

        # Function/class definitions
        if re.search(r'\bdef\s+\w+\s*\(', text):
            patterns.append("function definitions")
        if re.search(r'\bclass\s+\w+', text):
            patterns.append("class definitions")
        if re.search(r'\basync\s+def\b', text):
            patterns.append("async functions")
        if re.search(r'\bimport\s+|from\s+\w+\s+import', text):
            patterns.append("module imports")

        # Architecture patterns
        if re.search(r'\bdecorator\b|@\w+', text):
            patterns.append("decorators")
        if re.search(r'\bcontext\s+manager\b|with\s+', text):
            patterns.append("context managers")
        if re.search(r'\btype\s+hint|typing\.', text):
            patterns.append("type hints")
        if re.search(r'\btest\b|spec\b', text, re.IGNORECASE):
            patterns.append("testing")

        # API patterns
        if re.search(r'\b@app\.(route|get|post|put|delete)', text):
            patterns.append("REST API endpoints")
        if re.search(r'\bgraphql\b|Query\b|Mutation\b', text):
            patterns.append("GraphQL")
        if re.search(r'\bgrpc\b|@rpc\b', text):
            patterns.append("gRPC")

        # Database patterns
        if re.search(r'\bSELECT\s+|INSERT\s+|UPDATE\s+|DELETE\s+', text, re.IGNORECASE):
            patterns.append("SQL queries")
        if re.search(r'\bORM\b|SQLAlchemy|Prisma|Sequelize|Mongoose', text, re.IGNORECASE):
            patterns.append("ORM usage")

        # Configuration patterns
        if re.search(r'\bconfig\b|settings\b|env\b', text, re.IGNORECASE):
            patterns.append("configuration management")
        if re.search(r'\blogger\b|logging\.', text):
            patterns.append("logging")

        return patterns[:20]

    @staticmethod
    def _extract_technologies(text: str) -> list[str]:
        """Extract technology names from text."""
        tech_patterns = [
            r'\b(React|Vue|Angular|Svelte|Next\.?js|Nuxt)\b',
            r'\b(Python|Rust|Go|TypeScript|JavaScript|Java|C\+\+|Ruby|Swift|Kotlin)\b',
            r'\b(FastAPI|Django|Flask|Express|Actix|Gin|Spring)\b',
            r'\b(PostgreSQL|MySQL|MongoDB|Redis|SQLite|DynamoDB)\b',
            r'\b(Docker|Kubernetes|Terraform|AWS|GCP|Azure)\b',
            r'\b(TensorFlow|PyTorch|LangChain|LlamaIndex|Ollama|vLLM)\b',
            r'\b(Tailwind|shadcn|Prisma|Drizzle|tRPC|GraphQL)\b',
        ]
        found = set()
        for pattern in tech_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            found.update(m if isinstance(m, str) else m[0] for m in matches)
        return sorted(found)[:20]

    @staticmethod
    def _extract_concepts(text: str) -> list[str]:
        """Extract key concepts from headings and bold text."""
        headings = re.findall(r'^#{1,3}\s+(.+)$', text, re.MULTILINE)
        bold = re.findall(r'\*\*(.+?)\*\*', text)
        concepts = list(dict.fromkeys(headings[:10] + bold[:10]))
        return concepts[:15]

    # ── Output: Create wiki articles and ingest context ───────────────

    async def _create_wiki_articles(self, repo_name: str, knowledge: dict,
                                     pass_number: int) -> list[str]:
        """Create wiki articles from extracted knowledge."""
        from backend.wiki_service import wiki_service

        articles = []
        safe_name = repo_name.replace("/", "-")

        if pass_number == 1:
            # First pass: overview article
            content = self._build_overview_article(knowledge)
            slug = f"repo-{safe_name}"
            try:
                wiki_service.create_article(
                    title=f"Repository: {repo_name}",
                    content=content,
                    tags=["auto-learned", "github"] + knowledge.get("topics", [])[:3],
                )
                articles.append(slug)
            except Exception as e:
                logger.warning("Wiki article creation failed for %s: %s", repo_name, e)

        elif pass_number == 2:
            # Second pass: technical deep-dive
            content = self._build_technical_article(knowledge)
            try:
                wiki_service.create_article(
                    title=f"Technical: {repo_name}",
                    content=content,
                    tags=["auto-learned", "technical", "deep-dive"],
                )
                articles.append(f"tech-{safe_name}")
            except Exception:
                pass

        elif pass_number == 3:
            # Third pass: patterns and lessons
            content = self._build_patterns_article(knowledge)
            try:
                wiki_service.create_article(
                    title=f"Patterns: {repo_name}",
                    content=content,
                    tags=["auto-learned", "patterns", "lessons"],
                )
                articles.append(f"patterns-{safe_name}")
            except Exception:
                pass

        elif pass_number == 4:
            # Fourth pass: utilities and helpers
            content = self._build_utilities_article(knowledge)
            try:
                wiki_service.create_article(
                    title=f"Utilities: {repo_name}",
                    content=content,
                    tags=["auto-learned", "utilities", "helpers"],
                )
                articles.append(f"utils-{safe_name}")
            except Exception:
                pass

        elif pass_number == 5:
            # Fifth pass: examples and templates
            content = self._build_examples_article(knowledge)
            try:
                wiki_service.create_article(
                    title=f"Examples: {repo_name}",
                    content=content,
                    tags=["auto-learned", "examples", "templates"],
                )
                articles.append(f"examples-{safe_name}")
            except Exception:
                pass

        return articles

    def _build_overview_article(self, k: dict) -> str:
        parts = [
            f"# {k['name']}\n",
            f"**Language:** {k.get('language', 'Unknown')}  ",
            f"**Stars:** {k.get('stars', 0)}  ",
            f"**Topics:** {', '.join(k.get('topics', []))}  ",
            f"\n## Description\n{k.get('description', '')}",
            f"\n## Technologies\n{', '.join(k.get('technologies', []))}",
        ]
        if k.get("structure_summary"):
            parts.append(f"\n## Structure\n```\n{k['structure_summary']}\n```")
        if k.get("readme_summary"):
            parts.append(f"\n## README (excerpt)\n{k['readme_summary'][:1500]}")
        parts.append(f"\n---\n*Auto-learned by Oak on {k.get('extracted_at', '')}*")
        return "\n".join(parts)

    def _build_technical_article(self, k: dict) -> str:
        parts = [f"# Technical Analysis: {k['name']}\n"]
        if k.get("key_concepts"):
            parts.append("## Key Concepts\n" + "\n".join(f"- {c}" for c in k["key_concepts"]))
        if k.get("key_files"):
            parts.append("\n## Key Files Analyzed")
            for fname, content in k["key_files"].items():
                parts.append(f"\n### `{fname}`\n```\n{content[:800]}\n```")
        parts.append(f"\n---\n*Pass 2 deep-dive by Oak on {k.get('extracted_at', '')}*")
        return "\n".join(parts)

    def _build_patterns_article(self, k: dict) -> str:
        parts = [
            f"# Patterns & Lessons: {k['name']}\n",
            "## Architecture Patterns Observed",
        ]
        if k.get("technologies"):
            parts.append(f"\n**Tech Stack:** {', '.join(k['technologies'])}")
        if k.get("key_files"):
            parts.append("\n## Documentation & Tests")
            for fname, content in k["key_files"].items():
                parts.append(f"\n### `{fname}`\n{content[:500]}")
        parts.append(f"\n---\n*Pass 3 patterns extraction by Oak on {k.get('extracted_at', '')}*")
        return "\n".join(parts)

    def _build_utilities_article(self, k: dict) -> str:
        parts = [
            f"# Utilities & Helpers: {k['name']}\n",
            "## Utility Functions and Helper Modules",
        ]
        if k.get("key_files"):
            parts.append("\n## Key Utility Files")
            for fname, content in k["key_files"].items():
                parts.append(f"\n### `{fname}`\n{content[:600]}")
        parts.append(f"\n---\n*Pass 4 utilities extraction by Oak on {k.get('extracted_at', '')}*")
        return "\n".join(parts)

    def _build_examples_article(self, k: dict) -> str:
        parts = [
            f"# Examples & Templates: {k['name']}\n",
            "## Example Code and Template Files",
        ]
        if k.get("key_files"):
            parts.append("\n## Example Files")
            for fname, content in k["key_files"].items():
                parts.append(f"\n### `{fname}`\n{content[:600]}")
        parts.append(f"\n---\n*Pass 5 examples extraction by Oak on {k.get('extracted_at', '')}*")
        return "\n".join(parts)

    async def _ingest_context(self, repo_name: str, knowledge: dict,
                               pass_number: int) -> int:
        """Ingest knowledge into tiered context engine."""
        from backend.agent.tiered_context import tiered_context

        facts = 0
        uri = f"oak://learned/{repo_name.replace('/', '-')}/pass{pass_number}"

        content = json.dumps({
            "description": knowledge.get("description", ""),
            "technologies": knowledge.get("technologies", []),
            "concepts": knowledge.get("key_concepts", []),
            "language": knowledge.get("language", ""),
        }, indent=2)

        tiered_context.ingest(
            uri=uri,
            title=f"{repo_name} (pass {pass_number})",
            content=content,
            source="auto_learner",
            tags=["learned", f"pass{pass_number}"] + knowledge.get("topics", [])[:3],
        )
        facts += 1

        # Also add individual tech facts to memory
        from backend.agent.memory import agent_memory
        for tech in knowledge.get("technologies", [])[:5]:
            if agent_memory.memory_add("memory",
                    f"Repo {repo_name} uses {tech} ({knowledge.get('language', '')} project, "
                    f"{knowledge.get('stars', 0)} stars)").get("success"):
                facts += 1

        return facts

    def _store_learning(self, repo_name: str, knowledge: dict, pass_number: int):
        """Append to learning log (JSONL)."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "repo": repo_name,
            "pass_number": pass_number,
            "technologies": knowledge.get("technologies", []),
            "concepts_count": len(knowledge.get("key_concepts", [])),
        }
        with open(LEARNING_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    # ── Daily Run: The full learning cycle ────────────────────────────

    async def run_daily(self) -> dict:
        """Execute the full daily learning cycle."""
        if self._running:
            return {"error": "Learning cycle already running"}

        self._running = True
        start = time.time()
        report = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "repos_discovered": 0,
            "repos_processed": 0,
            "repos_skipped": 0,
            "files_analyzed": 0,
            "articles_created": 0,
            "facts_extracted": 0,
            "errors": [],
            "results": [],
        }

        try:
            from backend.agent.audit_log import audit_log

            # Step 1: Discover
            repos = await self.discover_trending_repos(limit=20)
            report["repos_discovered"] = len(repos)
            logger.info("Daily learning: discovered %d repos", len(repos))

            # Step 2: Process each
            for repo in repos:
                # Fetch commit for update detection
                commit = await self._fetch_latest_commit(repo["name"])
                if commit:
                    repo["commit"] = commit

                if not self.tracker.should_process(repo["url"], repo.get("commit", "")):
                    report["repos_skipped"] += 1
                    continue

                result = await self.process_repo(repo)
                report["results"].append(result)

                if result.get("success"):
                    report["repos_processed"] += 1
                    report["files_analyzed"] += result.get("files_analyzed", 0)
                    report["articles_created"] += result.get("articles_created", 0)
                    report["facts_extracted"] += result.get("facts_extracted", 0)
                elif result.get("error"):
                    report["errors"].append(f"{repo['name']}: {result['error']}")

                # Rate limit: wait between repos (conservative to stay within 5000/hour)
                await asyncio.sleep(3)

            report["duration_seconds"] = round(time.time() - start, 1)
            report["completed_at"] = datetime.now(timezone.utc).isoformat()

            # Save daily report
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            report_file = DAILY_REPORT / f"{date_str}.json"
            report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

            # Audit log
            audit_log.log(
                audit_log.TOOL_CALL,
                f"Daily learning: {report['repos_processed']} repos, "
                f"{report['files_analyzed']} files, "
                f"{report['articles_created']} articles, {report['facts_extracted']} facts",
                {"duration": report["duration_seconds"]},
                source="auto_learner",
            )

            self._last_run = report["completed_at"]
            self._last_report = report
            logger.info("Daily learning complete: %d repos, %d files, %d articles, %d facts in %.1fs",
                        report["repos_processed"], report["files_analyzed"],
                        report["articles_created"], report["facts_extracted"],
                        report["duration_seconds"])

        except Exception as e:
            logger.error("Daily learning failed: %s", e)
            report["error"] = str(e)
        finally:
            self._running = False

        return report

    # ── Status & API ──────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "running": self._running,
            "last_run": self._last_run,
            "tracker": self.tracker.stats(),
            "last_report_summary": {
                k: v for k, v in self._last_report.items()
                if k not in ("results", "errors")
            } if self._last_report else None,
        }

    def get_processed_repos(self) -> dict:
        return self.tracker.get_all()

    def get_daily_reports(self, limit: int = 7) -> list[dict]:
        """Get recent daily reports."""
        reports = []
        files = sorted(DAILY_REPORT.glob("*.json"), reverse=True)[:limit]
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                reports.append({
                    "date": f.stem,
                    "repos_processed": data.get("repos_processed", 0),
                    "articles_created": data.get("articles_created", 0),
                    "facts_extracted": data.get("facts_extracted", 0),
                    "duration_seconds": data.get("duration_seconds", 0),
                })
            except Exception:
                pass
        return reports


auto_learner = AutoLearner()
