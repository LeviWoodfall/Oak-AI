"""
Self-Improvement Engine — Oak learns and improves itself.

This module enables Oak to:
1. Extract skills and patterns from analyzed repositories
2. Store learned skills in a structured format
3. Apply learned skills to its own codebase
4. Self-code improvements using the built-in IDE

Skills are stored as markdown files in the skills directory and can be
applied to generate code improvements, refactoring suggestions, and new features.
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List
from backend.config import DATA_DIR
from backend.ide_service import ide_service

logger = logging.getLogger("oak.self_improver")

SKILLS_DIR = DATA_DIR / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

SKILL_INDEX = SKILLS_DIR / "index.json"


class Skill:
    """Represents a learned skill or pattern."""

    def __init__(self, name: str, category: str, pattern: str,
                 description: str, code_example: str, source_repo: str,
                 tags: List[str] = None):
        self.name = name
        self.category = category
        self.pattern = pattern
        self.description = description
        self.code_example = code_example
        self.source_repo = source_repo
        self.tags = tags or []
        self.learned_at = datetime.now(timezone.utc).isoformat()
        self.applied_count = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "pattern": self.pattern,
            "description": self.description,
            "code_example": self.code_example,
            "source_repo": self.source_repo,
            "tags": self.tags,
            "learned_at": self.learned_at,
            "applied_count": self.applied_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Skill':
        skill = cls(
            name=data["name"],
            category=data["category"],
            pattern=data["pattern"],
            description=data["description"],
            code_example=data["code_example"],
            source_repo=data["source_repo"],
            tags=data.get("tags", []),
        )
        skill.learned_at = data.get("learned_at", skill.learned_at)
        skill.applied_count = data.get("applied_count", 0)
        return skill


class SkillExtractor:
    """Extracts skills and patterns from analyzed code."""

    def __init__(self):
        self._skills: Dict[str, Skill] = {}
        self._load_index()

    def _load_index(self):
        if SKILL_INDEX.exists():
            try:
                with open(SKILL_INDEX, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for skill_data in data.get("skills", []):
                        skill = Skill.from_dict(skill_data)
                        self._skills[skill.name] = skill
            except Exception as e:
                logger.error("Failed to load skill index: %s", e)

    def _save_index(self):
        data = {
            "skills": [skill.to_dict() for skill in self._skills.values()],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(SKILL_INDEX, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)

    def extract_from_knowledge(self, repo_name: str, knowledge: dict) -> List[Skill]:
        """Extract skills from repository knowledge."""
        skills = []
        code_patterns = knowledge.get("code_patterns", [])
        key_files = knowledge.get("key_files", {})
        language = knowledge.get("language", "")

        # Extract utility functions
        for file_path, content in key_files.items():
            if "util" in file_path.lower() or "helper" in file_path.lower():
                self._extract_utility_skills(repo_name, file_path, content, language, skills)

        # Extract architectural patterns
        for pattern in code_patterns:
            self._extract_pattern_skills(repo_name, pattern, language, skills)

        # Extract error handling patterns
        self._extract_error_handling_skills(repo_name, key_files, language, skills)

        # Extract testing patterns
        self._extract_testing_skills(repo_name, key_files, language, skills)

        # Save new skills
        for skill in skills:
            if skill.name not in self._skills:
                self._skills[skill.name] = skill
                self._save_skill_file(skill)
                logger.info("Learned new skill: %s from %s", skill.name, repo_name)

        if skills:
            self._save_index()

        return skills

    def _extract_utility_skills(self, repo_name: str, file_path: str,
                                 content: str, language: str, skills: List[Skill]):
        """Extract utility functions as reusable skills."""
        if language.lower() == "python":
            # Extract Python utility functions
            func_pattern = r'def\s+(\w+)\s*\(([^)]*)\)\s*->\s*([^\s:]+):'
            for match in re.finditer(func_pattern, content):
                func_name = match.group(1)
                params = match.group(2)
                return_type = match.group(3)

                # Get function docstring
                doc_pattern = rf'def\s+{re.escape(func_name)}\s*\([^)]*\)\s*->\s*{re.escape(return_type)}:\s*"""([^"]*)"""'
                doc_match = re.search(doc_pattern, content, re.DOTALL)
                description = doc_match.group(1).strip() if doc_match else f"Utility function: {func_name}"

                # Get function body (simplified)
                skill = Skill(
                    name=f"{func_name}_utility",
                    category="utility",
                    pattern=f"def {func_name}({params}) -> {return_type}",
                    description=description[:500],
                    code_example=content[:1000],
                    source_repo=repo_name,
                    tags=["utility", "function", language],
                )
                skills.append(skill)

    def _extract_pattern_skills(self, repo_name: str, pattern: str,
                                language: str, skills: List[Skill]):
        """Extract architectural patterns as skills."""
        pattern_mapping = {
            "decorators": "Decorator Pattern",
            "context managers": "Context Manager Pattern",
            "type hints": "Type Hinting Best Practice",
            "async functions": "Async/Await Pattern",
            "REST API endpoints": "REST API Design Pattern",
            "GraphQL": "GraphQL API Pattern",
            "ORM usage": "ORM Pattern",
            "configuration management": "Configuration Management Pattern",
            "logging": "Logging Pattern",
        }

        if pattern in pattern_mapping:
            skill_name = f"{pattern_mapping[pattern]}_{language}"
            if skill_name not in [s.name for s in skills]:
                skill = Skill(
                    name=skill_name,
                    category="architecture",
                    pattern=pattern,
                    description=f"Architectural pattern: {pattern_mapping[pattern]} in {language}",
                    code_example=f"# Example of {pattern} in {language}",
                    source_repo=repo_name,
                    tags=["architecture", pattern, language],
                )
                skills.append(skill)

    def _extract_error_handling_skills(self, repo_name: str, key_files: Dict[str, str],
                                       language: str, skills: List[Skill]):
        """Extract error handling patterns."""
        if language.lower() == "python":
            for file_path, content in key_files.items():
                # Check for custom exceptions
                if re.search(r'class\s+\w+Exception\(\s*Exception\s*\)', content):
                    skill = Skill(
                        name=f"custom_exception_{repo_name.replace('/', '_')}",
                        category="error_handling",
                        pattern="Custom Exception Class",
                        description="Custom exception class for specific error handling",
                        code_example=content[:800],
                        source_repo=repo_name,
                        tags=["error_handling", "exception", language],
                    )
                    if skill.name not in [s.name for s in skills]:
                        skills.append(skill)

    def _extract_testing_skills(self, repo_name: str, key_files: Dict[str, str],
                               language: str, skills: List[Skill]):
        """Extract testing patterns."""
        for file_path, content in key_files.items():
            if "test" in file_path.lower():
                if language.lower() == "python":
                    if "pytest" in content or "unittest" in content:
                        skill = Skill(
                            name=f"test_pattern_{repo_name.replace('/', '_')}",
                            category="testing",
                            pattern="Testing Framework Pattern",
                            description=f"Testing pattern using {language} test frameworks",
                            code_example=content[:800],
                            source_repo=repo_name,
                            tags=["testing", language],
                        )
                        if skill.name not in [s.name for s in skills]:
                            skills.append(skill)

    def _save_skill_file(self, skill: Skill):
        """Save skill as a markdown file."""
        skill_file = SKILLS_DIR / f"{skill.name}.md"
        content = f"""# {skill.name}

**Category:** {skill.category}
**Source:** {skill.source_repo}
**Learned:** {skill.learned_at}
**Tags:** {', '.join(skill.tags)}

## Description
{skill.description}

## Pattern
```
{skill.pattern}
```

## Code Example
```{skill.category}
{skill.code_example}
```

## Applied Count
{skill.applied_count}
"""
        skill_file.write_text(content, encoding='utf-8')

    def get_skill(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_skills(self, category: str = None) -> List[Skill]:
        skills = list(self._skills.values())
        if category:
            skills = [s for s in skills if s.category == category]
        return sorted(skills, key=lambda s: s.learned_at, reverse=True)

    def get_relevant_skills(self, context: str, limit: int = 5) -> List[Skill]:
        """Get skills relevant to a given context."""
        relevant = []
        context_lower = context.lower()

        for skill in self._skills.values():
            # Check if skill tags or description match context
            if any(tag.lower() in context_lower for tag in skill.tags):
                relevant.append(skill)
            elif skill.pattern.lower() in context_lower:
                relevant.append(skill)
            elif skill.description.lower() in context_lower:
                relevant.append(skill)

        return sorted(relevant, key=lambda s: s.applied_count, reverse=True)[:limit]

    def mark_applied(self, skill_name: str):
        """Mark a skill as applied."""
        if skill_name in self._skills:
            self._skills[skill_name].applied_count += 1
            self._save_index()
            logger.info("Skill %s applied (count: %d)", skill_name, self._skills[skill_name].applied_count)


class SelfCoder:
    """Applies learned skills to Oak's own codebase."""

    def __init__(self, skill_extractor: SkillExtractor):
        self.skill_extractor = skill_extractor
        self.oak_codebase = Path(__file__).parent.parent.parent
        self.proposals_dir = DATA_DIR / "self_improvement_proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)

    def generate_improvement_proposal(self, skill: Skill, target_file: str = None) -> dict:
        """Generate a code improvement proposal based on a learned skill."""
        proposal = {
            "skill_name": skill.name,
            "skill_category": skill.category,
            "description": f"Apply {skill.name} to improve codebase",
            "pattern": skill.pattern,
            "suggested_changes": [],
            "target_files": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }

        # Find relevant files in Oak's codebase
        if skill.category == "utility":
            proposal["target_files"] = self._find_utility_files()
        elif skill.category == "error_handling":
            proposal["target_files"] = self._find_files_needing_error_handling()
        elif skill.category == "testing":
            proposal["target_files"] = self._find_files_needing_tests()
        else:
            proposal["target_files"] = [target_file] if target_file else []

        # Generate specific change suggestions
        for target_file in proposal["target_files"][:3]:  # Limit to 3 files
            change = {
                "file": target_file,
                "suggestion": f"Consider applying {skill.pattern} to this file",
                "reason": skill.description,
            }
            proposal["suggested_changes"].append(change)

        return proposal

    def _find_utility_files(self) -> List[str]:
        """Find utility files in Oak's codebase."""
        utility_files = []
        for path in self.oak_codebase.rglob("*.py"):
            if "util" in str(path).lower() or "helper" in str(path).lower():
                utility_files.append(str(path.relative_to(self.oak_codebase)))
        return utility_files[:10]

    def _find_files_needing_error_handling(self) -> List[str]:
        """Find files that might need better error handling."""
        files = []
        for path in self.oak_codebase.rglob("*.py"):
            # Skip test files and __init__ files
            if "test" in str(path).lower() or path.name == "__init__.py":
                continue
            try:
                content = path.read_text(encoding='utf-8')
                # Check for bare except clauses
                if re.search(r'except\s*:', content):
                    files.append(str(path.relative_to(self.oak_codebase)))
            except Exception:
                pass
        return files[:10]

    def _find_files_needing_tests(self) -> List[str]:
        """Find files without corresponding test files."""
        files = []
        for path in self.oak_codebase.rglob("*.py"):
            # Skip test files and __init__ files
            if "test" in str(path).lower() or path.name == "__init__.py":
                continue
            # Check if test file exists
            test_path = path.parent / f"test_{path.name}"
            if not test_path.exists():
                files.append(str(path.relative_to(self.oak_codebase)))
        return files[:10]

    def save_proposal(self, proposal: dict) -> str:
        """Save a proposal to disk."""
        proposal_id = f"proposal_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        proposal_file = self.proposals_dir / f"{proposal_id}.json"
        proposal["id"] = proposal_id
        with open(proposal_file, 'w', encoding='utf-8') as f:
            json.dump(proposal, f, indent=2, default=str)
        return proposal_id

    def list_proposals(self, status: str = None) -> List[dict]:
        """List all improvement proposals."""
        proposals = []
        for proposal_file in self.proposals_dir.glob("proposal_*.json"):
            try:
                with open(proposal_file, 'r', encoding='utf-8') as f:
                    proposal = json.load(f)
                    if status is None or proposal.get("status") == status:
                        proposals.append(proposal)
            except Exception as e:
                logger.error("Failed to load proposal %s: %s", proposal_file, e)
        return sorted(proposals, key=lambda p: p["created_at"], reverse=True)

    def apply_proposal(self, proposal_id: str) -> dict:
        """Apply a proposal (mark as applied, actual implementation requires review)."""
        proposal_file = self.proposals_dir / f"{proposal_id}.json"
        if not proposal_file.exists():
            return {"error": "Proposal not found"}

        with open(proposal_file, 'r', encoding='utf-8') as f:
            proposal = json.load(f)

        proposal["status"] = "approved"
        proposal["applied_at"] = datetime.now(timezone.utc).isoformat()

        with open(proposal_file, 'w', encoding='utf-8') as f:
            json.dump(proposal, f, indent=2, default=str)

        # Mark skill as applied
        self.skill_extractor.mark_applied(proposal["skill_name"])

        return {"status": "approved", "proposal_id": proposal_id}

    def apply_code_change(self, file_path: str, old_text: str, new_text: str) -> dict:
        """Apply a code change using the IDE service."""
        if not ide_service.file_exists(file_path):
            return {"error": "File not found", "path": file_path}

        success = ide_service.apply_diff(file_path, old_text, new_text)
        if success:
            logger.info("Applied code change to %s", file_path)
            return {"status": "applied", "path": file_path}
        else:
            return {"error": "Failed to apply change", "path": file_path}

    async def generate_code_from_skill(self, skill_name: str, context: str) -> dict:
        """Generate code based on a learned skill and context."""
        skill = self.skill_extractor.get_skill(skill_name)
        if not skill:
            return {"error": "Skill not found"}

        # Use the LLM to generate code based on the skill
        from backend.llm_service import llm_service

        prompt = f"""You are Oak, a self-improving AI. Generate code based on the following skill and context.

Skill: {skill.name}
Category: {skill.category}
Pattern: {skill.pattern}
Description: {skill.description}

Example from skill:
```{skill.category}
{skill.code_example}
```

Context: {context}

Generate code that applies this skill to the given context. Output ONLY the code, no explanations:"""

        full_response = ""
        async for chunk in llm_service.generate(prompt, temperature=0.3):
            try:
                data = json.loads(chunk)
                if "response" in data:
                    full_response += data["response"]
                if data.get("done"):
                    break
            except json.JSONDecodeError:
                pass

        # Clean up response
        if full_response.strip().startswith("```"):
            lines = full_response.strip().split("\n")
            full_response = "\n".join(lines[1:-1]) if full_response.strip().endswith("```") else "\n".join(lines[1:])

        return {
            "skill_name": skill_name,
            "generated_code": full_response.strip(),
            "status": "generated",
        }


# Global instances
skill_extractor = SkillExtractor()
self_coder = SelfCoder(skill_extractor)
