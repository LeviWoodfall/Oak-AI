"""
Agent Skills — markdown-based skill loader and executor.
Inspired by superpowers (workflow methodology), deer-flow (progressive loading),
and everything-claude-code (4-layer: agents → skills → hooks → rules).

Skills are markdown files that define:
- A name and description (YAML frontmatter)
- Step-by-step workflow instructions
- Tool hints (which tools the skill needs)
- Verification criteria
"""
import logging
import re
from pathlib import Path
from typing import Optional
import frontmatter
from backend.config import BASE_DIR, DATA_DIR

logger = logging.getLogger("oak.agent.skills")

BUILTIN_SKILLS_DIR = BASE_DIR / "backend" / "skills"
USER_SKILLS_DIR = DATA_DIR / "skills"
USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)


class Skill:
    """Parsed skill from a markdown file."""

    def __init__(self, slug: str, title: str, description: str, content: str,
                 tools: list[str] = None, tags: list[str] = None,
                 trigger: str = "", source: str = "builtin"):
        self.slug = slug
        self.title = title
        self.description = description
        self.content = content
        self.tools = tools or []
        self.tags = tags or []
        self.trigger = trigger  # slash command, e.g. "/brainstorm"
        self.source = source

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "tools": self.tools,
            "tags": self.tags,
            "trigger": self.trigger,
            "source": self.source,
        }

    def to_prompt(self) -> str:
        """Convert skill to an LLM-ready instruction block."""
        return (
            f"## Active Skill: {self.title}\n\n"
            f"{self.content}\n\n"
            f"Available tools for this skill: {', '.join(self.tools) if self.tools else 'all'}\n"
        )


class SkillLoader:
    """Loads and manages skills from builtin and user directories."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._load_all()

    def _load_all(self):
        """Load skills from both builtin and user directories."""
        for directory, source in [(BUILTIN_SKILLS_DIR, "builtin"), (USER_SKILLS_DIR, "user")]:
            if not directory.exists():
                continue
            for f in sorted(directory.glob("*.md")):
                try:
                    skill = self._parse_skill(f, source)
                    self._skills[skill.slug] = skill
                except Exception as e:
                    logger.warning("Failed to load skill %s: %s", f.name, e)
        logger.info("Loaded %d skills (%d builtin, %d user)",
                     len(self._skills),
                     sum(1 for s in self._skills.values() if s.source == "builtin"),
                     sum(1 for s in self._skills.values() if s.source == "user"))

    def _parse_skill(self, filepath: Path, source: str) -> Skill:
        """Parse a markdown skill file with YAML frontmatter."""
        post = frontmatter.load(str(filepath))
        slug = filepath.stem
        return Skill(
            slug=slug,
            title=post.get("title", slug.replace("-", " ").title()),
            description=post.get("description", ""),
            content=post.content,
            tools=post.get("tools", []),
            tags=post.get("tags", []),
            trigger=post.get("trigger", f"/{slug}"),
            source=source,
        )

    def reload(self):
        """Reload all skills from disk."""
        self._skills.clear()
        self._load_all()

    def list_skills(self) -> list[dict]:
        """List all available skills."""
        return [s.to_dict() for s in self._skills.values()]

    def get(self, slug: str) -> Optional[Skill]:
        """Get a skill by slug."""
        return self._skills.get(slug)

    def find_by_trigger(self, trigger: str) -> Optional[Skill]:
        """Find a skill by its slash command trigger."""
        for skill in self._skills.values():
            if skill.trigger == trigger:
                return skill
        return None

    def find_relevant(self, task_description: str, max_skills: int = 3) -> list[Skill]:
        """Find skills relevant to a task description (keyword matching).
        Progressive loading — only return what's needed."""
        words = set(task_description.lower().split())
        scored = []
        for skill in self._skills.values():
            skill_words = set(
                skill.title.lower().split() +
                skill.description.lower().split() +
                skill.tags
            )
            overlap = len(words & skill_words)
            if overlap > 0:
                scored.append((overlap, skill))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:max_skills]]

    def create_skill(self, slug: str, title: str, description: str,
                     content: str, tools: list[str] = None,
                     tags: list[str] = None) -> Skill:
        """Create a new user skill."""
        filepath = USER_SKILLS_DIR / f"{slug}.md"
        post = frontmatter.Post(
            content,
            title=title,
            description=description,
            tools=tools or [],
            tags=tags or [],
            trigger=f"/{slug}",
        )
        filepath.write_text(frontmatter.dumps(post), encoding="utf-8")
        skill = Skill(
            slug=slug, title=title, description=description,
            content=content, tools=tools or [], tags=tags or [],
            trigger=f"/{slug}", source="user",
        )
        self._skills[slug] = skill
        logger.info("Created skill: %s", slug)
        return skill

    def delete_skill(self, slug: str) -> bool:
        """Delete a user skill (cannot delete builtins)."""
        skill = self._skills.get(slug)
        if not skill or skill.source != "user":
            return False
        filepath = USER_SKILLS_DIR / f"{slug}.md"
        if filepath.exists():
            filepath.unlink()
        del self._skills[slug]
        return True


skill_loader = SkillLoader()
