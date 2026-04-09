"""
Self-Improvement Engine — Oak's ability to recognize gaps, research solutions,
acquire skills from GitHub or create them, and continuously improve.

Inspired by:
- hermes-agent: self-improving skills from experience
- autoresearch: autonomous experimentation loops
- deer-flow: progressive skill loading
- anthropics/skills + vercel-labs/skills: standard SKILL.md format

When the agent encounters something it can't do:
1. Search GitHub for relevant skills (npx skills ecosystem)
2. Download and install matching SKILL.md files
3. Or generate a new skill from scratch using the LLM
4. Document everything in the audit log
"""
import json
import logging
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import httpx
from backend.config import DATA_DIR, settings
from backend.agent.audit_log import audit_log

logger = logging.getLogger("oak.agent.self_improve")

INSTALLED_SKILLS_DIR = DATA_DIR / "skills"
INSTALLED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

# Well-known skill repositories to search
SKILL_REPOS = [
    "anthropics/skills",
    "vercel-labs/agent-skills",
    "VoltAgent/awesome-openclaw-skills",
    "openclaw/skills",
    "ComposioHQ/awesome-claude-skills",
    "phuryn/pm-skills",
]


class SelfImproveEngine:
    """Manages skill acquisition, creation, and self-improvement."""

    def __init__(self):
        self._http = httpx.AsyncClient(timeout=30)
        self._github_api = "https://api.github.com"

    # ── Skill Gap Detection ──────────────────────────────────────────

    async def assess_capability(self, task_description: str, available_skills: list[dict]) -> dict:
        """Assess whether the agent has the skills to handle a task.
        Returns a gap analysis with recommendations."""
        skill_names = [s.get("title", s.get("slug", "")) for s in available_skills]
        skill_tags = []
        for s in available_skills:
            skill_tags.extend(s.get("tags", []))

        # Keyword extraction from task
        task_words = set(task_description.lower().split())
        common_skill_domains = {
            "test": "testing", "debug": "debugging", "deploy": "deployment",
            "docker": "containerization", "api": "api-development",
            "database": "database", "auth": "authentication", "css": "styling",
            "react": "react", "vue": "vue", "frontend": "frontend",
            "backend": "backend", "ml": "machine-learning", "data": "data-processing",
            "scrape": "web-scraping", "email": "email", "pdf": "pdf-processing",
            "excel": "spreadsheet", "automate": "automation", "workflow": "workflow",
        }

        gaps = []
        for word, domain in common_skill_domains.items():
            if word in task_words and domain not in skill_tags and domain not in " ".join(skill_names).lower():
                gaps.append(domain)

        return {
            "task": task_description[:200],
            "available_skills": len(available_skills),
            "detected_gaps": gaps,
            "has_gaps": len(gaps) > 0,
            "recommendation": f"Search for skills: {', '.join(gaps)}" if gaps else "No gaps detected",
        }

    # ── GitHub Skill Search ──────────────────────────────────────────

    async def search_github_skills(self, query: str, limit: int = 10) -> list[dict]:
        """Search GitHub for skills matching a query."""
        results = []
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = settings.github_token
        if token:
            headers["Authorization"] = f"token {token}"

        try:
            # Search for SKILL.md files in repos
            search_query = f"{query} filename:SKILL.md"
            resp = await self._http.get(
                f"{self._github_api}/search/code",
                params={"q": search_query, "per_page": limit},
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("items", [])[:limit]:
                    results.append({
                        "name": item.get("name", ""),
                        "path": item.get("path", ""),
                        "repo": item.get("repository", {}).get("full_name", ""),
                        "url": item.get("html_url", ""),
                        "score": item.get("score", 0),
                    })

            # Also search repos with "skills" + query
            resp2 = await self._http.get(
                f"{self._github_api}/search/repositories",
                params={"q": f"{query} skills", "sort": "stars", "per_page": 5},
                headers=headers,
            )
            if resp2.status_code == 200:
                for repo in resp2.json().get("items", []):
                    results.append({
                        "name": repo["name"],
                        "path": "",
                        "repo": repo["full_name"],
                        "url": repo["html_url"],
                        "score": repo.get("stargazers_count", 0),
                        "description": repo.get("description", ""),
                        "type": "repo",
                    })

        except Exception as e:
            logger.error("GitHub skill search failed: %s", e)

        audit_log.log(
            audit_log.SELF_RESEARCH,
            f"Searched GitHub for skills: {query}",
            {"query": query, "results_count": len(results)},
        )
        return results

    # ── Skill Installation from GitHub ───────────────────────────────

    async def install_skill_from_github(self, repo: str, skill_path: str = "") -> Optional[dict]:
        """Download and install a SKILL.md from a GitHub repo.
        repo: 'owner/repo'
        skill_path: path within the repo (e.g. 'skills/my-skill')
        """
        headers = {"Accept": "application/vnd.github.v3.raw"}
        token = settings.github_token
        if token:
            headers["Authorization"] = f"token {token}"

        # Determine the SKILL.md URL
        if skill_path:
            raw_path = f"{skill_path}/SKILL.md" if not skill_path.endswith("SKILL.md") else skill_path
        else:
            raw_path = "SKILL.md"

        url = f"{self._github_api}/repos/{repo}/contents/{raw_path}"

        try:
            resp = await self._http.get(url, headers=headers)
            if resp.status_code != 200:
                return None

            content_data = resp.json()
            import base64
            if content_data.get("encoding") == "base64":
                skill_content = base64.b64decode(content_data["content"]).decode("utf-8")
            else:
                skill_content = content_data.get("content", "")

            # Parse the SKILL.md
            import frontmatter
            post = frontmatter.loads(skill_content)
            name = post.get("name", skill_path.split("/")[-1] if skill_path else repo.split("/")[-1])
            slug = re.sub(r"[^\w\s-]", "", name.lower()).replace(" ", "-")[:60]

            # Save to installed skills directory
            skill_dir = INSTALLED_SKILLS_DIR / slug
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")

            # Save metadata
            meta = {
                "source_repo": repo,
                "source_path": skill_path,
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "name": name,
                "slug": slug,
            }
            (skill_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

            audit_log.log(
                audit_log.SKILL_INSTALLED,
                f"Installed skill '{name}' from {repo}",
                {"repo": repo, "path": skill_path, "slug": slug},
            )

            # Reload skills
            from backend.agent.skills import skill_loader
            skill_loader.reload()

            return {"slug": slug, "name": name, "source": repo, "status": "installed"}

        except Exception as e:
            logger.error("Skill install failed: %s", e)
            audit_log.log(audit_log.ERROR, f"Skill install failed: {e}", {"repo": repo, "path": skill_path})
            return None

    # ── Skill Creation (AI-generated) ────────────────────────────────

    async def create_skill_for_task(self, task_description: str, context: str = "") -> dict:
        """Generate a new skill using the LLM when no existing skill fits."""
        from backend.llm_service import llm_service

        prompt = f"""You are creating a new agent skill. Generate a SKILL.md file for the following task.

Task: {task_description}
{f'Context: {context}' if context else ''}

Output a complete SKILL.md file with YAML frontmatter (name, description) and markdown body
containing: purpose, when to use, step-by-step workflow, guidelines, and examples.
Follow the anthropics/skills standard format.

Output ONLY the SKILL.md content, nothing else:"""

        full_response = ""
        async for chunk in llm_service.generate(prompt, temperature=0.5):
            try:
                data = json.loads(chunk)
                if "response" in data:
                    full_response += data["response"]
                if data.get("done"):
                    break
            except json.JSONDecodeError:
                pass

        if not full_response.strip():
            return {"status": "error", "error": "LLM returned empty response"}

        # Parse the generated skill
        import frontmatter
        try:
            # Clean up — strip code fences if present
            content = full_response.strip()
            if content.startswith("```"):
                content = "\n".join(content.split("\n")[1:])
            if content.endswith("```"):
                content = content[:-3].strip()

            post = frontmatter.loads(content)
            name = post.get("name", "generated-skill")
            slug = re.sub(r"[^\w\s-]", "", name.lower()).replace(" ", "-")[:60]
            description = post.get("description", task_description[:100])

            # Save
            from backend.agent.skills import skill_loader
            skill = skill_loader.create_skill(
                slug=slug,
                title=name.replace("-", " ").title(),
                description=description,
                content=post.content,
                tools=post.get("tools", []),
                tags=post.get("tags", ["ai-generated"]),
            )

            audit_log.log(
                audit_log.SKILL_CREATED,
                f"AI generated skill '{name}' for task: {task_description[:80]}",
                {"slug": slug, "task": task_description[:200]},
            )

            return {"slug": slug, "name": name, "status": "created", "skill": skill.to_dict()}

        except Exception as e:
            logger.error("Skill creation parse failed: %s", e)
            return {"status": "error", "error": str(e)}

    # ── Auto-Improvement Loop ────────────────────────────────────────

    async def auto_improve(self, task_description: str) -> dict:
        """Full self-improvement cycle:
        1. Assess gaps
        2. Search GitHub for matching skills
        3. Install if found, or create a new one
        4. Log everything
        """
        from backend.agent.skills import skill_loader

        # 1. Assess
        available = skill_loader.list_skills()
        assessment = await self.assess_capability(task_description, available)

        if not assessment["has_gaps"]:
            return {"action": "none", "reason": "No skill gaps detected", "assessment": assessment}

        # 2. Search GitHub
        for gap in assessment["detected_gaps"][:3]:
            results = await self.search_github_skills(gap, limit=5)
            if results:
                # Try to install the best match
                for r in results:
                    if r.get("type") != "repo" and r.get("repo"):
                        installed = await self.install_skill_from_github(
                            r["repo"], r.get("path", "").replace("/SKILL.md", "")
                        )
                        if installed:
                            audit_log.log(
                                audit_log.SELF_IMPROVE,
                                f"Auto-installed skill for gap '{gap}' from {r['repo']}",
                                {"gap": gap, "skill": installed},
                            )
                            return {
                                "action": "installed",
                                "gap": gap,
                                "skill": installed,
                                "source": "github",
                            }

        # 3. No GitHub skill found — create one
        created = await self.create_skill_for_task(task_description)
        if created.get("status") == "created":
            audit_log.log(
                audit_log.SELF_IMPROVE,
                f"Auto-created skill for gaps: {assessment['detected_gaps']}",
                {"gaps": assessment["detected_gaps"], "skill": created},
            )
            return {
                "action": "created",
                "gaps": assessment["detected_gaps"],
                "skill": created,
                "source": "ai-generated",
            }

        return {
            "action": "failed",
            "gaps": assessment["detected_gaps"],
            "error": created.get("error", "Unknown"),
        }

    # ── List installed skills from GitHub ─────────────────────────────

    def list_installed(self) -> list[dict]:
        """List all skills installed from GitHub."""
        installed = []
        for skill_dir in sorted(INSTALLED_SKILLS_DIR.iterdir()):
            meta_file = skill_dir / "meta.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    installed.append(meta)
                except Exception:
                    pass
        return installed


self_improve_engine = SelfImproveEngine()
