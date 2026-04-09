"""
Autonomous Learning Engine — Oak's growth engine.
Like a tree absorbing sunlight, Oak absorbs knowledge from the internet daily.

Daily cycle:
  1. Discover top trending GitHub repos (multiple sources)
  2. Clone/analyze each repo (README, structure, key patterns)
  3. Extract knowledge → build wiki articles + ingest into tiered context
  4. Track processing count per repo (max 3 passes, then skip)
  5. Log everything to audit trail

The internet is the sun. Oak grows every day.
"""
import asyncio
import json
import logging
import hashlib
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import httpx
from backend.config import DATA_DIR, settings

logger = logging.getLogger("oak.learner")

LEARNER_DIR = DATA_DIR / "learner"
LEARNER_DIR.mkdir(parents=True, exist_ok=True)
PROCESS_LOG = LEARNER_DIR / "process_log.json"
LEARNING_LOG = LEARNER_DIR / "learning_log.jsonl"
DAILY_REPORT = LEARNER_DIR / "daily_reports"
DAILY_REPORT.mkdir(parents=True, exist_ok=True)

MAX_PROCESS_COUNT = 3  # Process each repo up to 3 times, then skip


class RepoTracker:
    """Tracks how many times each repo has been processed."""

    def __init__(self):
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self):
        if PROCESS_LOG.exists():
            try:
                self._data = json.loads(PROCESS_LOG.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def _save(self):
        PROCESS_LOG.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")

    def get_count(self, repo_url: str) -> int:
        key = self._key(repo_url)
        return self._data.get(key, {}).get("count", 0)

    def should_process(self, repo_url: str) -> bool:
        return self.get_count(repo_url) < MAX_PROCESS_COUNT

    def record_processing(self, repo_url: str, success: bool, articles_created: int = 0,
                          facts_extracted: int = 0):
        key = self._key(repo_url)
        entry = self._data.get(key, {"count": 0, "url": repo_url, "history": []})
        entry["count"] = entry.get("count", 0) + 1
        entry["last_processed"] = datetime.now(timezone.utc).isoformat()
        entry["history"].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": success,
            "articles_created": articles_created,
            "facts_extracted": facts_extracted,
            "pass_number": entry["count"],
        })
        self._data[key] = entry
        self._save()

    def get_all(self) -> dict:
        return self._data

    def stats(self) -> dict:
        total = len(self._data)
        completed = sum(1 for v in self._data.values() if v.get("count", 0) >= MAX_PROCESS_COUNT)
        in_progress = total - completed
        return {"total_repos_seen": total, "completed_3x": completed, "in_progress": in_progress}

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

    # ── Discovery: Find top repos from multiple sources ───────────────

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
        """Analyze a single repo: fetch README, structure, extract knowledge."""
        url = repo["url"]
        name = repo["name"]
        result = {"repo": name, "url": url, "articles_created": 0,
                  "facts_extracted": 0, "success": False, "pass_number": 0}

        if not self.tracker.should_process(url):
            result["skipped"] = True
            result["reason"] = f"Already processed {MAX_PROCESS_COUNT} times"
            return result

        pass_number = self.tracker.get_count(url) + 1
        result["pass_number"] = pass_number
        logger.info("Processing repo %s (pass %d/%d)", name, pass_number, MAX_PROCESS_COUNT)

        try:
            # Fetch README
            readme_content = await self._fetch_readme(name)

            # Fetch repo structure (top-level files/dirs)
            structure = await self._fetch_structure(name)

            # Fetch key files based on pass number
            key_files = await self._fetch_key_files(name, pass_number)

            # Build knowledge from what we gathered
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

            result["success"] = True
            self.tracker.record_processing(url, True, len(articles), facts)

        except Exception as e:
            logger.error("Failed to process %s: %s", name, e)
            result["error"] = str(e)
            self.tracker.record_processing(url, False)

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
        except Exception:
            pass
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
        except Exception:
            pass
        return []

    async def _fetch_key_files(self, repo_name: str, pass_number: int) -> dict[str, str]:
        """Fetch key files based on pass number for progressive learning."""
        headers = {"Accept": "application/vnd.github.v3.raw"}
        if settings.github_token:
            headers["Authorization"] = f"token {settings.github_token}"

        # Pass 1: README + config files
        # Pass 2: Source code samples
        # Pass 3: Tests + docs
        targets = {
            1: ["package.json", "pyproject.toml", "Cargo.toml", "setup.py",
                "requirements.txt", "CONTRIBUTING.md"],
            2: ["src/main.py", "src/index.ts", "src/lib.rs", "main.go",
                "app.py", "index.js", "src/main.rs"],
            3: ["CHANGELOG.md", "docs/README.md", "tests/test_main.py",
                "test/index.test.ts", "ARCHITECTURE.md"],
        }

        files = {}
        for filename in targets.get(pass_number, targets[1]):
            try:
                resp = await self._http.get(
                    f"https://api.github.com/repos/{repo_name}/contents/{filename}",
                    headers=headers,
                )
                if resp.status_code == 200:
                    files[filename] = resp.text[:5000]
                await asyncio.sleep(0.5)  # Rate limit
            except Exception:
                pass
        return files

    def _extract_knowledge(self, repo: dict, readme: str, structure: list[str],
                           key_files: dict, pass_number: int) -> dict:
        """Extract structured knowledge from repo data."""
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

        return knowledge

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
                if not self.tracker.should_process(repo["url"]):
                    report["repos_skipped"] += 1
                    continue

                result = await self.process_repo(repo)
                report["results"].append(result)

                if result.get("success"):
                    report["repos_processed"] += 1
                    report["articles_created"] += result.get("articles_created", 0)
                    report["facts_extracted"] += result.get("facts_extracted", 0)
                elif result.get("error"):
                    report["errors"].append(f"{repo['name']}: {result['error']}")

                # Rate limit: wait between repos
                await asyncio.sleep(2)

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
                f"{report['articles_created']} articles, {report['facts_extracted']} facts",
                {"duration": report["duration_seconds"]},
                source="auto_learner",
            )

            self._last_run = report["completed_at"]
            self._last_report = report
            logger.info("Daily learning complete: %d repos, %d articles, %d facts in %.1fs",
                        report["repos_processed"], report["articles_created"],
                        report["facts_extracted"], report["duration_seconds"])

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
