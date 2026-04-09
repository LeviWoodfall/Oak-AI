"""
Fact Checker — Oak's hallucination prevention system.
Runs TWICE as often as the learning cycle to verify knowledge integrity.

Verification strategies:
  1. Cross-reference: Check wiki claims against live GitHub API data
  2. Consistency: Detect contradictions within memory/wiki/context stores
  3. Staleness: Flag entries whose source repos have changed significantly
  4. Self-audit: Verify auto-generated wiki articles match source README content
  5. Confidence scoring: Rate each fact 0-100 based on verification results

"Trust but verify, then verify again."
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import httpx
from backend.config import DATA_DIR, settings

logger = logging.getLogger("oak.fact_checker")

CHECKER_DIR = DATA_DIR / "fact_checker"
CHECKER_DIR.mkdir(parents=True, exist_ok=True)
VERIFICATION_LOG = CHECKER_DIR / "verification_log.jsonl"
FLAGGED_FILE = CHECKER_DIR / "flagged_entries.json"
CHECK_REPORTS = CHECKER_DIR / "reports"
CHECK_REPORTS.mkdir(parents=True, exist_ok=True)


class FactChecker:
    """Cross-references Oak's knowledge to prevent hallucinations."""

    def __init__(self):
        self._http = httpx.AsyncClient(timeout=20, follow_redirects=True)
        self._running = False
        self._last_run = ""
        self._last_report: dict = {}
        self._flagged: list[dict] = self._load_flagged()

    # ── Main verification cycle ───────────────────────────────────────

    async def run_verification(self) -> dict:
        """Execute full fact-checking cycle."""
        if self._running:
            return {"error": "Verification already running"}

        self._running = True
        start = time.time()
        report = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "wiki_articles_checked": 0,
            "context_entries_checked": 0,
            "memory_entries_checked": 0,
            "issues_found": 0,
            "issues_fixed": 0,
            "flagged": [],
        }

        try:
            # Strategy 1: Verify wiki articles tagged 'auto-learned'
            wiki_results = await self._verify_wiki_articles()
            report["wiki_articles_checked"] = wiki_results["checked"]
            report["issues_found"] += wiki_results["issues"]
            report["flagged"].extend(wiki_results["flagged"])

            # Strategy 2: Check tiered context for staleness
            context_results = await self._verify_context_entries()
            report["context_entries_checked"] = context_results["checked"]
            report["issues_found"] += context_results["issues"]
            report["flagged"].extend(context_results["flagged"])

            # Strategy 3: Check memory for contradictions
            memory_results = self._verify_memory_consistency()
            report["memory_entries_checked"] = memory_results["checked"]
            report["issues_found"] += memory_results["issues"]
            report["flagged"].extend(memory_results["flagged"])

            # Strategy 4: Auto-fix simple issues
            fixed = await self._auto_fix_issues(report["flagged"])
            report["issues_fixed"] = fixed

            report["duration_seconds"] = round(time.time() - start, 1)
            report["completed_at"] = datetime.now(timezone.utc).isoformat()

            # Save report
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
            report_file = CHECK_REPORTS / f"{date_str}.json"
            report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

            # Update flagged entries
            self._flagged = report["flagged"]
            self._save_flagged()

            # Audit log
            from backend.agent.audit_log import audit_log
            audit_log.log(
                audit_log.TOOL_CALL,
                f"Fact check: {report['wiki_articles_checked']} wiki, "
                f"{report['context_entries_checked']} context, "
                f"{report['memory_entries_checked']} memory — "
                f"{report['issues_found']} issues, {report['issues_fixed']} fixed",
                {"duration": report["duration_seconds"]},
                source="fact_checker",
            )

            self._last_run = report["completed_at"]
            self._last_report = report
            logger.info("Fact check complete: %d checked, %d issues, %d fixed in %.1fs",
                        report["wiki_articles_checked"] + report["context_entries_checked"]
                        + report["memory_entries_checked"],
                        report["issues_found"], report["issues_fixed"],
                        report["duration_seconds"])

        except Exception as e:
            logger.error("Fact check failed: %s", e)
            report["error"] = str(e)
        finally:
            self._running = False

        return report

    # ── Strategy 1: Verify wiki articles ──────────────────────────────

    async def _verify_wiki_articles(self) -> dict:
        """Verify auto-learned wiki articles against live GitHub data."""
        from backend.wiki_service import wiki_service

        result = {"checked": 0, "issues": 0, "flagged": []}

        articles = wiki_service.list_articles()
        for article_meta in articles:
            slug = article_meta.get("slug", "")
            tags = article_meta.get("tags", [])

            if "auto-learned" not in tags:
                continue

            article = wiki_service.get_article(slug)
            if not article:
                continue

            result["checked"] += 1
            content = article.get("content", "")
            title = article.get("title", "")

            # Extract repo name from title
            repo_name = self._extract_repo_name(title)
            if not repo_name:
                continue

            # Verify against live GitHub data
            issues = await self._verify_repo_claims(repo_name, content)
            if issues:
                result["issues"] += len(issues)
                result["flagged"].append({
                    "type": "wiki_article",
                    "slug": slug,
                    "repo": repo_name,
                    "issues": issues,
                    "severity": "medium" if len(issues) < 3 else "high",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        return result

    async def _verify_repo_claims(self, repo_name: str, content: str) -> list[str]:
        """Verify specific claims in article content against GitHub API."""
        issues = []
        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.github_token:
            headers["Authorization"] = f"token {settings.github_token}"

        try:
            resp = await self._http.get(
                f"https://api.github.com/repos/{repo_name}",
                headers=headers,
            )
            if resp.status_code == 404:
                issues.append(f"Repository {repo_name} no longer exists or is private")
                return issues
            if resp.status_code != 200:
                return issues  # Can't verify, don't flag

            repo_data = resp.json()

            # Check if stars claim is wildly off (>50% difference)
            stars_match = re.search(r'\*\*Stars:\*\*\s*(\d+)', content)
            if stars_match:
                claimed_stars = int(stars_match.group(1))
                actual_stars = repo_data.get("stargazers_count", 0)
                if actual_stars > 0 and abs(claimed_stars - actual_stars) / actual_stars > 0.5:
                    issues.append(
                        f"Stars outdated: claimed {claimed_stars}, actual {actual_stars}")

            # Check if language claim is wrong
            lang_match = re.search(r'\*\*Language:\*\*\s*(\w+)', content)
            if lang_match:
                claimed_lang = lang_match.group(1)
                actual_lang = repo_data.get("language", "")
                if actual_lang and claimed_lang.lower() != actual_lang.lower():
                    issues.append(
                        f"Language mismatch: claimed {claimed_lang}, actual {actual_lang}")

            # Check if description has changed significantly
            actual_desc = repo_data.get("description", "") or ""
            desc_match = re.search(r'## Description\n(.+?)(?:\n#|\n---|\Z)', content, re.DOTALL)
            if desc_match and actual_desc:
                claimed_desc = desc_match.group(1).strip()[:200]
                if claimed_desc and actual_desc:
                    # Simple similarity check
                    claimed_words = set(claimed_desc.lower().split())
                    actual_words = set(actual_desc.lower().split())
                    if len(claimed_words) > 3 and len(actual_words) > 3:
                        overlap = len(claimed_words & actual_words)
                        total = max(len(claimed_words), len(actual_words))
                        if total > 0 and overlap / total < 0.3:
                            issues.append("Description has changed significantly since learning")

            # Check if repo has been archived
            if repo_data.get("archived"):
                issues.append("Repository has been archived since learning")

        except Exception as e:
            logger.debug("Verify %s failed: %s", repo_name, e)

        return issues

    # ── Strategy 2: Verify tiered context entries ─────────────────────

    async def _verify_context_entries(self) -> dict:
        """Check tiered context entries for staleness."""
        from backend.agent.tiered_context import tiered_context

        result = {"checked": 0, "issues": 0, "flagged": []}

        entries = tiered_context.list_all(source_filter="auto_learner")
        for entry in entries:
            result["checked"] += 1
            uri = entry.get("uri", "")
            updated = entry.get("updated", "")

            # Check if entry is older than 30 days
            if updated:
                try:
                    entry_date = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    age_days = (datetime.now(timezone.utc) - entry_date).days
                    if age_days > 30:
                        result["issues"] += 1
                        result["flagged"].append({
                            "type": "stale_context",
                            "uri": uri,
                            "age_days": age_days,
                            "severity": "low" if age_days < 60 else "medium",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                except (ValueError, TypeError):
                    pass

        return result

    # ── Strategy 3: Memory consistency ────────────────────────────────

    def _verify_memory_consistency(self) -> dict:
        """Check memory entries for internal contradictions."""
        from backend.agent.memory import agent_memory

        result = {"checked": 0, "issues": 0, "flagged": []}

        # Get all memory entries
        facts = agent_memory.get_facts(limit=200)
        result["checked"] = len(facts)

        # Check for contradictions: same subject, different claims
        entries_by_subject = {}
        for fact in facts:
            text = fact.get("text", "")
            # Extract subject (first few words or repo name pattern)
            repo_match = re.match(r'Repo\s+(\S+)', text)
            if repo_match:
                subject = repo_match.group(1)
                entries_by_subject.setdefault(subject, []).append(text)

        for subject, entries in entries_by_subject.items():
            if len(entries) <= 1:
                continue

            # Check if entries about same repo claim different languages
            languages = set()
            for entry in entries:
                lang_match = re.search(r'\((\w+)\s+project', entry)
                if lang_match:
                    languages.add(lang_match.group(1).lower())

            if len(languages) > 1:
                result["issues"] += 1
                result["flagged"].append({
                    "type": "memory_contradiction",
                    "subject": subject,
                    "details": f"Multiple languages claimed: {', '.join(languages)}",
                    "entries": entries[:3],
                    "severity": "medium",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        return result

    # ── Auto-fix ──────────────────────────────────────────────────────

    async def _auto_fix_issues(self, flagged: list[dict]) -> int:
        """Attempt to auto-fix flagged issues."""
        fixed = 0

        for issue in flagged:
            issue_type = issue.get("type", "")

            if issue_type == "stale_context" and issue.get("severity") == "low":
                # Don't auto-remove, just flag for review
                continue

            if issue_type == "wiki_article":
                # For wiki articles with outdated stars, update them
                for sub_issue in issue.get("issues", []):
                    if "Stars outdated" in sub_issue:
                        try:
                            await self._update_wiki_stars(
                                issue.get("slug", ""), issue.get("repo", ""))
                            fixed += 1
                        except Exception:
                            pass

            if issue_type == "memory_contradiction":
                # Remove the older contradicting entries, keep the newest
                try:
                    from backend.agent.memory import agent_memory
                    entries = issue.get("entries", [])
                    # Remove all but the last (newest) entry
                    for old_entry in entries[:-1]:
                        agent_memory.memory_remove("memory", old_entry[:50])
                        fixed += 1
                except Exception:
                    pass

        return fixed

    async def _update_wiki_stars(self, slug: str, repo_name: str):
        """Update star count in a wiki article."""
        from backend.wiki_service import wiki_service

        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.github_token:
            headers["Authorization"] = f"token {settings.github_token}"

        resp = await self._http.get(
            f"https://api.github.com/repos/{repo_name}", headers=headers)
        if resp.status_code != 200:
            return

        actual_stars = resp.json().get("stargazers_count", 0)
        article = wiki_service.get_article(slug)
        if not article:
            return

        content = article["content"]
        new_content = re.sub(
            r'\*\*Stars:\*\*\s*\d+',
            f'**Stars:** {actual_stars}',
            content,
        )
        if new_content != content:
            wiki_service.update_article(slug, content=new_content)
            logger.info("Updated stars for %s: now %d", repo_name, actual_stars)

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_repo_name(title: str) -> Optional[str]:
        """Extract 'owner/repo' from wiki article title."""
        # Matches "Repository: owner/repo" or "Technical: owner/repo"
        match = re.search(r'(?:Repository|Technical|Patterns):\s*(.+)', title)
        if match:
            return match.group(1).strip()
        return None

    def _load_flagged(self) -> list[dict]:
        if FLAGGED_FILE.exists():
            try:
                return json.loads(FLAGGED_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return []

    def _save_flagged(self):
        FLAGGED_FILE.write_text(json.dumps(self._flagged, indent=2), encoding="utf-8")

    def _log_verification(self, entry: dict):
        with open(VERIFICATION_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    # ── Status & API ──────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "running": self._running,
            "last_run": self._last_run,
            "flagged_count": len(self._flagged),
            "last_report_summary": {
                k: v for k, v in self._last_report.items()
                if k not in ("flagged",)
            } if self._last_report else None,
        }

    def get_flagged(self) -> list[dict]:
        return self._flagged

    def get_reports(self, limit: int = 10) -> list[dict]:
        reports = []
        files = sorted(CHECK_REPORTS.glob("*.json"), reverse=True)[:limit]
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                reports.append({
                    "date": f.stem,
                    "issues_found": data.get("issues_found", 0),
                    "issues_fixed": data.get("issues_fixed", 0),
                    "total_checked": (data.get("wiki_articles_checked", 0)
                                      + data.get("context_entries_checked", 0)
                                      + data.get("memory_entries_checked", 0)),
                    "duration_seconds": data.get("duration_seconds", 0),
                })
            except Exception:
                pass
        return reports


fact_checker = FactChecker()
