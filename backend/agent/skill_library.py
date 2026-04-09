"""
Skill Library — Memento-inspired unified skill gateway for Oak.

Implements the Read → Execute → Reflect → Write loop:
  READ:    Hybrid retrieval (semantic vectors + keyword BM25) to find best skill
  EXECUTE: Run the skill via agent tools
  REFLECT: After execution, analyze success/failure
  WRITE:   On success → bump utility score. On failure → rewrite/improve skill.

Key concepts from Memento-Skills:
  - Skills are first-class units of capability (not just flat functions)
  - Every skill has a utility score that rises on success, triggers optimization on failure
  - Skills evolve through versioning — each improvement creates a new version
  - A content analyzer checks skill quality before storage
  - Unified gateway merges builtin SKILL.md + learned skills + AI-generated skills

"The point is not merely to add more tools. The point is to learn better skills
through task experience." — Memento-Skills
"""
import json
import logging
import re
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from backend.config import DATA_DIR

logger = logging.getLogger("oak.skill_library")

LIBRARY_DIR = DATA_DIR / "skill_library"
LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
SKILL_STORE = LIBRARY_DIR / "skills"
SKILL_STORE.mkdir(parents=True, exist_ok=True)
INDEX_FILE = LIBRARY_DIR / "index.json"
EVOLUTION_LOG = LIBRARY_DIR / "evolution.jsonl"

# Utility score thresholds
INITIAL_UTILITY = 50        # New skills start at 50
SUCCESS_BOOST = 10          # +10 on success
FAILURE_PENALTY = 15        # -15 on failure
OPTIMIZE_THRESHOLD = 30     # Below this → trigger skill optimization
RETIRE_THRESHOLD = 10       # Below this → retire the skill
PROMOTE_THRESHOLD = 80      # Above this → mark as proven


class SkillEntry:
    """A skill in the library with utility scoring and version tracking."""

    def __init__(self, skill_id: str, name: str, description: str,
                 content: str, category: str = "general",
                 source: str = "unknown", tags: list[str] = None,
                 tools: list[str] = None, trigger: str = "",
                 utility: int = INITIAL_UTILITY, version: int = 1,
                 executions: int = 0, successes: int = 0, failures: int = 0,
                 created_at: str = "", updated_at: str = "",
                 source_repo: str = "", status: str = "active"):
        self.skill_id = skill_id
        self.name = name
        self.description = description
        self.content = content
        self.category = category
        self.source = source  # builtin, learned, generated, github
        self.tags = tags or []
        self.tools = tools or []
        self.trigger = trigger
        self.utility = utility
        self.version = version
        self.executions = executions
        self.successes = successes
        self.failures = failures
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.updated_at = updated_at or self.created_at
        self.source_repo = source_repo
        self.status = status  # active, optimizing, retired, proven

    @property
    def success_rate(self) -> float:
        return self.successes / max(self.executions, 1)

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "content": self.content,
            "category": self.category,
            "source": self.source,
            "tags": self.tags,
            "tools": self.tools,
            "trigger": self.trigger,
            "utility": self.utility,
            "version": self.version,
            "executions": self.executions,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": round(self.success_rate, 2),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_repo": self.source_repo,
            "status": self.status,
        }

    def to_prompt(self) -> str:
        """Convert to LLM-ready instruction block."""
        return (
            f"## Active Skill: {self.name} (utility: {self.utility}, "
            f"success rate: {self.success_rate:.0%})\n\n"
            f"{self.content}\n\n"
            f"Available tools: {', '.join(self.tools) if self.tools else 'all'}\n"
        )

    @classmethod
    def from_dict(cls, data: dict) -> 'SkillEntry':
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__init__.__code__.co_varnames and k != 'self'})


class ContentAnalyzer:
    """Analyzes skill content for quality before storage.
    Inspired by Memento-Skills content_analyzer.py."""

    @staticmethod
    def analyze(skill: SkillEntry) -> dict:
        """Score skill quality 0-100 with specific issues."""
        score = 100
        issues = []

        # Check minimum content length
        if len(skill.content) < 50:
            score -= 30
            issues.append("Content too short (< 50 chars)")

        # Check for description
        if not skill.description or len(skill.description) < 10:
            score -= 15
            issues.append("Missing or too-short description")

        # Check for actionable instructions (headings, steps, code blocks)
        has_headings = bool(re.search(r'^#{1,3}\s+', skill.content, re.MULTILINE))
        has_code = '```' in skill.content or '    ' in skill.content
        has_steps = bool(re.search(r'^\d+\.\s+|^-\s+', skill.content, re.MULTILINE))

        if not has_headings and not has_steps:
            score -= 15
            issues.append("No structure (missing headings or numbered steps)")

        if not has_code and skill.category in ("utility", "architecture", "testing"):
            score -= 10
            issues.append("No code examples for technical skill")

        # Check for tags
        if not skill.tags:
            score -= 5
            issues.append("No tags")

        return {
            "score": max(0, score),
            "issues": issues,
            "pass": score >= 50,
        }


class SkillLibrary:
    """Unified skill gateway with Memento-inspired learning loop.

    Merges:
    - builtin SKILL.md files (from skills.py)
    - learned skills (from self_improver.py / auto_learner.py)
    - AI-generated skills (from self_improve.py)
    - GitHub-installed skills

    Adds:
    - Utility scoring per skill
    - Semantic + keyword hybrid retrieval
    - Reflect → Write loop (success boosts score, failure triggers rewrite)
    - Version tracking for skill evolution
    - Content quality analysis
    """

    def __init__(self):
        self._skills: dict[str, SkillEntry] = {}
        self._analyzer = ContentAnalyzer()
        self._vector_collection = None
        self._load_index()
        self._import_existing_skills()

    # ── Index persistence ─────────────────────────────────────────────

    def _load_index(self):
        """Load skill index from disk."""
        if INDEX_FILE.exists():
            try:
                data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
                for sd in data.get("skills", []):
                    try:
                        entry = SkillEntry.from_dict(sd)
                        self._skills[entry.skill_id] = entry
                    except Exception as e:
                        logger.warning("Failed to load skill %s: %s",
                                       sd.get("skill_id", "?"), e)
                logger.info("Loaded %d skills from library index", len(self._skills))
            except Exception as e:
                logger.error("Failed to load skill index: %s", e)

    def _save_index(self):
        """Persist skill index to disk."""
        data = {
            "skills": [s.to_dict() for s in self._skills.values()],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total": len(self._skills),
            "active": sum(1 for s in self._skills.values() if s.status == "active"),
            "proven": sum(1 for s in self._skills.values() if s.status == "proven"),
            "retired": sum(1 for s in self._skills.values() if s.status == "retired"),
        }
        INDEX_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _import_existing_skills(self):
        """One-time import of existing skills from skills.py and self_improver.py."""
        imported = 0

        # Import from skills.py (SKILL.md files)
        try:
            from backend.agent.skills import skill_loader
            for skill_dict in skill_loader.list_skills():
                sid = f"builtin-{skill_dict['slug']}"
                if sid not in self._skills:
                    skill_obj = skill_loader.get(skill_dict["slug"])
                    if skill_obj:
                        entry = SkillEntry(
                            skill_id=sid,
                            name=skill_obj.title,
                            description=skill_obj.description,
                            content=skill_obj.content,
                            category="builtin",
                            source="builtin",
                            tags=skill_obj.tags,
                            tools=skill_obj.tools,
                            trigger=skill_obj.trigger,
                            utility=75,  # Builtins start higher
                            status="proven",
                        )
                        self._skills[sid] = entry
                        imported += 1
        except Exception as e:
            logger.debug("Could not import builtin skills: %s", e)

        # Import from self_improver.py (learned skills)
        try:
            from backend.agent.self_improver import skill_extractor
            for skill in skill_extractor.list_skills():
                sid = f"learned-{skill.name}"
                if sid not in self._skills:
                    entry = SkillEntry(
                        skill_id=sid,
                        name=skill.name,
                        description=skill.description,
                        content=skill.code_example,
                        category=skill.category,
                        source="learned",
                        tags=skill.tags,
                        source_repo=skill.source_repo,
                        utility=INITIAL_UTILITY,
                    )
                    self._skills[sid] = entry
                    imported += 1
        except Exception as e:
            logger.debug("Could not import learned skills: %s", e)

        if imported > 0:
            self._save_index()
            logger.info("Imported %d existing skills into unified library", imported)

    # ── READ: Skill Routing (hybrid retrieval) ────────────────────────

    def route(self, task: str, max_results: int = 3) -> list[SkillEntry]:
        """Find the best skills for a task using hybrid retrieval.

        Combines:
        1. Keyword scoring (BM25-like term overlap)
        2. Semantic scoring (ChromaDB vector similarity)
        3. Utility weighting (higher utility = higher ranking)
        """
        if not self._skills:
            return []

        candidates = []
        task_lower = task.lower()
        task_words = set(task_lower.split())

        for skill in self._skills.values():
            if skill.status == "retired":
                continue

            # Keyword score (BM25-like term frequency)
            skill_words = set(
                skill.name.lower().split() +
                skill.description.lower().split() +
                [t.lower() for t in skill.tags]
            )
            keyword_overlap = len(task_words & skill_words)
            keyword_score = keyword_overlap / max(len(task_words), 1)

            # Trigger match (exact slash command)
            trigger_match = 0
            if skill.trigger and task_lower.startswith(skill.trigger):
                trigger_match = 1.0

            # Utility weight (normalized 0-1)
            utility_weight = skill.utility / 100

            # Combined score
            combined = (
                keyword_score * 0.4 +
                trigger_match * 0.3 +
                utility_weight * 0.3
            )

            if combined > 0.05 or trigger_match > 0:
                candidates.append((combined, skill))

        # Try semantic search if available and we need more candidates
        if len(candidates) < max_results:
            semantic_results = self._semantic_search(task, n=max_results)
            for score, skill in semantic_results:
                # Blend semantic score with utility
                combined = score * 0.5 + (skill.utility / 100) * 0.5
                # Avoid duplicates
                if not any(s.skill_id == skill.skill_id for _, s in candidates):
                    candidates.append((combined, skill))

        # Sort by combined score, descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [skill for _, skill in candidates[:max_results]]

    def _semantic_search(self, query: str, n: int = 5) -> list[tuple[float, SkillEntry]]:
        """Search skills using ChromaDB vector similarity."""
        try:
            col = self._get_vector_collection()
            if col.count() == 0:
                return []

            results = col.query(query_texts=[query], n_results=min(n, col.count()))
            if not results or not results.get("ids"):
                return []

            matches = []
            for doc_id, distance in zip(results["ids"][0], results["distances"][0]):
                score = 1 - distance  # cosine distance → similarity
                skill = self._skills.get(doc_id)
                if skill:
                    matches.append((score, skill))
            return matches
        except Exception as e:
            logger.debug("Semantic search failed: %s", e)
            return []

    def _get_vector_collection(self):
        """Get or create the skill vector collection."""
        if self._vector_collection is None:
            try:
                import chromadb
                from chromadb.config import Settings as ChromaSettings
                from backend.config import CHROMA_DIR
                client = chromadb.PersistentClient(
                    path=str(CHROMA_DIR),
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
                self._vector_collection = client.get_or_create_collection(
                    name="skill_library",
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception as e:
                logger.warning("Could not init skill vector collection: %s", e)
        return self._vector_collection

    def _index_skill_vectors(self, skill: SkillEntry):
        """Index a skill's content in ChromaDB for semantic retrieval."""
        try:
            col = self._get_vector_collection()
            if col is None:
                return
            # Combine name + description + tags for embedding
            text = f"{skill.name} {skill.description} {' '.join(skill.tags)} {skill.content[:500]}"
            col.upsert(
                ids=[skill.skill_id],
                documents=[text],
                metadatas=[{"name": skill.name, "category": skill.category,
                            "utility": skill.utility}],
            )
        except Exception as e:
            logger.debug("Failed to index skill vectors: %s", e)

    # ── EXECUTE: Record execution ─────────────────────────────────────

    def record_execution(self, skill_id: str, success: bool,
                         task: str = "", error: str = ""):
        """Record a skill execution result — the core of the learning loop.

        On success: boost utility score.
        On failure: penalize and potentially trigger optimization.
        """
        skill = self._skills.get(skill_id)
        if not skill:
            return

        skill.executions += 1
        skill.updated_at = datetime.now(timezone.utc).isoformat()

        if success:
            skill.successes += 1
            skill.utility = min(100, skill.utility + SUCCESS_BOOST)
            if skill.utility >= PROMOTE_THRESHOLD and skill.status == "active":
                skill.status = "proven"
                logger.info("Skill '%s' promoted to PROVEN (utility: %d)",
                            skill.name, skill.utility)
        else:
            skill.failures += 1
            skill.utility = max(0, skill.utility - FAILURE_PENALTY)

            if skill.utility <= RETIRE_THRESHOLD:
                skill.status = "retired"
                logger.info("Skill '%s' RETIRED (utility: %d, %d failures)",
                            skill.name, skill.utility, skill.failures)
            elif skill.utility <= OPTIMIZE_THRESHOLD:
                skill.status = "optimizing"
                logger.info("Skill '%s' flagged for OPTIMIZATION (utility: %d)",
                            skill.name, skill.utility)

        # Log evolution
        self._log_evolution({
            "skill_id": skill_id,
            "name": skill.name,
            "event": "success" if success else "failure",
            "utility": skill.utility,
            "version": skill.version,
            "status": skill.status,
            "task": task[:200] if task else "",
            "error": error[:200] if error else "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        self._save_index()

    # ── REFLECT → WRITE: Skill optimization ───────────────────────────

    async def reflect_and_improve(self, skill_id: str, task: str,
                                   error: str) -> Optional[SkillEntry]:
        """Reflect on a skill failure and attempt to improve it.

        This is the core Memento pattern:
        1. Analyze what went wrong
        2. Generate an improved version of the skill
        3. Create a new version with the improvements
        4. Reset utility to give the improved version a fair chance
        """
        skill = self._skills.get(skill_id)
        if not skill:
            return None

        if skill.source == "builtin":
            # Don't rewrite builtins, but log the issue
            logger.info("Builtin skill '%s' failed — logging for manual review", skill.name)
            return None

        logger.info("Reflecting on skill '%s' failure (v%d, utility: %d)",
                     skill.name, skill.version, skill.utility)

        try:
            from backend.llm_service import llm_service

            prompt = f"""You are Oak, a self-improving AI agent. A skill has failed and needs improvement.

## Failed Skill: {skill.name}
Category: {skill.category}
Current Version: {skill.version}
Utility Score: {skill.utility}/100
Success Rate: {skill.success_rate:.0%} ({skill.successes}/{skill.executions})

### Current Skill Content:
{skill.content[:2000]}

### Task That Failed:
{task[:500]}

### Error:
{error[:500]}

## Your Job:
Analyze why this skill failed and generate an IMPROVED version.
Focus on:
1. What specifically caused the failure
2. How to make the skill more robust
3. Better error handling or edge case coverage
4. Clearer instructions if the issue was ambiguity

Output the improved skill content as markdown. Start with a brief "## Improvement Notes"
section explaining what you changed and why, then the full improved skill content."""

            full_response = ""
            async for chunk in llm_service.generate(prompt, temperature=0.4):
                try:
                    data = json.loads(chunk)
                    if "response" in data:
                        full_response += data["response"]
                    if data.get("done"):
                        break
                except json.JSONDecodeError:
                    pass

            if not full_response.strip():
                logger.warning("LLM returned empty response for skill improvement")
                return None

            # Create improved version
            skill.version += 1
            skill.content = full_response.strip()
            skill.utility = INITIAL_UTILITY  # Reset for fair evaluation
            skill.status = "active"
            skill.updated_at = datetime.now(timezone.utc).isoformat()

            # Quality check
            analysis = self._analyzer.analyze(skill)
            if not analysis["pass"]:
                logger.warning("Improved skill '%s' v%d failed quality check: %s",
                               skill.name, skill.version, analysis["issues"])
                # Still save it, but mark issues
                skill.tags = list(set(skill.tags + ["needs-review"]))

            # Save and re-index
            self._save_skill_file(skill)
            self._index_skill_vectors(skill)
            self._save_index()

            # Log evolution
            self._log_evolution({
                "skill_id": skill.skill_id,
                "name": skill.name,
                "event": "improved",
                "old_version": skill.version - 1,
                "new_version": skill.version,
                "utility": skill.utility,
                "quality_score": analysis["score"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            logger.info("Improved skill '%s' → v%d (quality: %d/100)",
                         skill.name, skill.version, analysis["score"])

            # Audit log
            try:
                from backend.agent.audit_log import audit_log
                audit_log.log(
                    audit_log.SELF_IMPROVE,
                    f"Skill '{skill.name}' improved to v{skill.version} after failure",
                    {"skill_id": skill.skill_id, "version": skill.version,
                     "quality": analysis["score"]},
                    source="skill_library",
                )
            except Exception:
                pass

            return skill

        except Exception as e:
            logger.error("Failed to improve skill '%s': %s", skill.name, e)
            return None

    # ── Skill CRUD ────────────────────────────────────────────────────

    def add_skill(self, name: str, description: str, content: str,
                  category: str = "general", source: str = "unknown",
                  tags: list[str] = None, tools: list[str] = None,
                  trigger: str = "", source_repo: str = "") -> SkillEntry:
        """Add a new skill to the library."""
        skill_id = self._generate_id(name)

        # Check for duplicates
        if skill_id in self._skills:
            existing = self._skills[skill_id]
            logger.info("Skill '%s' already exists (v%d), updating",
                         name, existing.version)
            existing.content = content
            existing.description = description
            existing.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_skill_file(existing)
            self._index_skill_vectors(existing)
            self._save_index()
            return existing

        entry = SkillEntry(
            skill_id=skill_id,
            name=name,
            description=description,
            content=content,
            category=category,
            source=source,
            tags=tags or [],
            tools=tools or [],
            trigger=trigger,
            source_repo=source_repo,
        )

        # Quality check
        analysis = self._analyzer.analyze(entry)
        if not analysis["pass"]:
            logger.warning("New skill '%s' has quality issues: %s", name, analysis["issues"])
            entry.tags = list(set(entry.tags + ["needs-review"]))

        self._skills[skill_id] = entry
        self._save_skill_file(entry)
        self._index_skill_vectors(entry)
        self._save_index()

        logger.info("Added skill '%s' (id=%s, quality=%d, source=%s)",
                     name, skill_id, analysis["score"], source)
        return entry

    def get(self, skill_id: str) -> Optional[SkillEntry]:
        return self._skills.get(skill_id)

    def get_by_name(self, name: str) -> Optional[SkillEntry]:
        for skill in self._skills.values():
            if skill.name == name:
                return skill
        return None

    def get_by_trigger(self, trigger: str) -> Optional[SkillEntry]:
        for skill in self._skills.values():
            if skill.trigger and skill.trigger == trigger:
                return skill
        return None

    def remove(self, skill_id: str) -> bool:
        skill = self._skills.get(skill_id)
        if not skill or skill.source == "builtin":
            return False
        del self._skills[skill_id]
        # Remove vector
        try:
            col = self._get_vector_collection()
            if col:
                col.delete(ids=[skill_id])
        except Exception:
            pass
        # Remove file
        skill_file = SKILL_STORE / f"{skill_id}.json"
        if skill_file.exists():
            skill_file.unlink()
        self._save_index()
        return True

    # ── Bulk queries ──────────────────────────────────────────────────

    def list_all(self, category: str = None, source: str = None,
                 status: str = None) -> list[dict]:
        """List all skills with optional filters."""
        results = []
        for skill in self._skills.values():
            if category and skill.category != category:
                continue
            if source and skill.source != source:
                continue
            if status and skill.status != status:
                continue
            results.append(skill.to_dict())
        return sorted(results, key=lambda x: x["utility"], reverse=True)

    def get_skills_needing_optimization(self) -> list[SkillEntry]:
        """Get skills flagged for optimization."""
        return [s for s in self._skills.values() if s.status == "optimizing"]

    def get_proven_skills(self) -> list[SkillEntry]:
        """Get high-utility proven skills."""
        return [s for s in self._skills.values()
                if s.status == "proven" and s.utility >= PROMOTE_THRESHOLD]

    # ── Persistence helpers ───────────────────────────────────────────

    def _save_skill_file(self, skill: SkillEntry):
        """Save individual skill to disk."""
        skill_file = SKILL_STORE / f"{skill.skill_id}.json"
        skill_file.write_text(json.dumps(skill.to_dict(), indent=2), encoding="utf-8")

    def _log_evolution(self, entry: dict):
        """Append to evolution log (JSONL)."""
        with open(EVOLUTION_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    @staticmethod
    def _generate_id(name: str) -> str:
        slug = re.sub(r"[^\w\s-]", "", name.lower()).replace(" ", "-")[:40]
        return slug or hashlib.md5(name.encode()).hexdigest()[:12]

    # ── Stats & API ───────────────────────────────────────────────────

    def stats(self) -> dict:
        total = len(self._skills)
        by_status = {}
        by_source = {}
        by_category = {}
        total_executions = 0
        total_successes = 0

        for s in self._skills.values():
            by_status[s.status] = by_status.get(s.status, 0) + 1
            by_source[s.source] = by_source.get(s.source, 0) + 1
            by_category[s.category] = by_category.get(s.category, 0) + 1
            total_executions += s.executions
            total_successes += s.successes

        avg_utility = (sum(s.utility for s in self._skills.values()) / max(total, 1))

        return {
            "total_skills": total,
            "by_status": by_status,
            "by_source": by_source,
            "by_category": by_category,
            "avg_utility": round(avg_utility, 1),
            "total_executions": total_executions,
            "total_successes": total_successes,
            "overall_success_rate": round(total_successes / max(total_executions, 1), 2),
        }

    def get_evolution_history(self, skill_id: str = None,
                              limit: int = 50) -> list[dict]:
        """Get evolution history, optionally filtered by skill."""
        if not EVOLUTION_LOG.exists():
            return []
        entries = []
        try:
            for line in EVOLUTION_LOG.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                entry = json.loads(line)
                if skill_id and entry.get("skill_id") != skill_id:
                    continue
                entries.append(entry)
        except Exception:
            pass
        return entries[-limit:]

    def get_leaderboard(self, limit: int = 20) -> list[dict]:
        """Get top skills ranked by utility and success rate."""
        ranked = sorted(
            self._skills.values(),
            key=lambda s: (s.utility, s.success_rate, s.executions),
            reverse=True,
        )
        return [
            {
                "skill_id": s.skill_id,
                "name": s.name,
                "utility": s.utility,
                "success_rate": round(s.success_rate, 2),
                "executions": s.executions,
                "version": s.version,
                "status": s.status,
                "category": s.category,
            }
            for s in ranked[:limit]
        ]


# Global instance
skill_library = SkillLibrary()
