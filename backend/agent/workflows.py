"""
Workflow Automation — define, schedule, and run recurring tasks.
Automates monotonous work: file processing, code formatting, backups,
report generation, data syncing, etc.

Workflows are JSON-defined task sequences that can:
- Run on a schedule (cron-like interval)
- Be triggered manually
- Chain multiple agent tools together
- Log all executions to audit
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from backend.config import DATA_DIR
from backend.agent.audit_log import audit_log

logger = logging.getLogger("oak.agent.workflows")

WORKFLOWS_DIR = DATA_DIR / "workflows"
WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
WORKFLOW_LOG = WORKFLOWS_DIR / "execution_log.jsonl"


class Workflow:
    """A defined automation workflow."""

    def __init__(self, id: str, name: str, description: str, steps: list[dict],
                 schedule: str = "", enabled: bool = True, tags: list[str] = None,
                 created: str = "", last_run: str = "", run_count: int = 0):
        self.id = id
        self.name = name
        self.description = description
        self.steps = steps  # [{tool: "tool_name", params: {...}}, ...]
        self.schedule = schedule  # "every 1h", "every 30m", "daily", "manual"
        self.enabled = enabled
        self.tags = tags or []
        self.created = created or datetime.now(timezone.utc).isoformat()
        self.last_run = last_run
        self.run_count = run_count

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "steps": self.steps,
            "schedule": self.schedule,
            "enabled": self.enabled,
            "tags": self.tags,
            "created": self.created,
            "last_run": self.last_run,
            "run_count": self.run_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Workflow":
        return cls(**d)


class WorkflowEngine:
    """Manages workflow CRUD, execution, and scheduling."""

    def __init__(self):
        self._workflows: dict[str, Workflow] = {}
        self._load_all()
        self._running_tasks: dict[str, asyncio.Task] = {}

    # ── CRUD ─────────────────────────────────────────────────────────

    def _load_all(self):
        for f in WORKFLOWS_DIR.glob("*.json"):
            if f.name == "execution_log.jsonl":
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                wf = Workflow.from_dict(data)
                self._workflows[wf.id] = wf
            except Exception as e:
                logger.warning("Failed to load workflow %s: %s", f.name, e)
        logger.info("Loaded %d workflows", len(self._workflows))

    def _save(self, wf: Workflow):
        filepath = WORKFLOWS_DIR / f"{wf.id}.json"
        filepath.write_text(json.dumps(wf.to_dict(), indent=2), encoding="utf-8")

    def create(self, name: str, description: str, steps: list[dict],
               schedule: str = "manual", tags: list[str] = None) -> Workflow:
        """Create a new workflow."""
        wf_id = f"wf-{int(time.time())}"
        wf = Workflow(
            id=wf_id, name=name, description=description,
            steps=steps, schedule=schedule, tags=tags or [],
        )
        self._workflows[wf_id] = wf
        self._save(wf)
        audit_log.log(
            audit_log.WORKFLOW_CREATED,
            f"Created workflow: {name}",
            {"id": wf_id, "steps": len(steps), "schedule": schedule},
        )
        return wf

    def get(self, wf_id: str) -> Optional[Workflow]:
        return self._workflows.get(wf_id)

    def list_all(self) -> list[dict]:
        return [wf.to_dict() for wf in self._workflows.values()]

    def update(self, wf_id: str, **kwargs) -> Optional[Workflow]:
        wf = self._workflows.get(wf_id)
        if not wf:
            return None
        for key, value in kwargs.items():
            if hasattr(wf, key) and value is not None:
                setattr(wf, key, value)
        self._save(wf)
        return wf

    def delete(self, wf_id: str) -> bool:
        if wf_id not in self._workflows:
            return False
        filepath = WORKFLOWS_DIR / f"{wf_id}.json"
        if filepath.exists():
            filepath.unlink()
        del self._workflows[wf_id]
        return True

    # ── Execution ────────────────────────────────────────────────────

    async def run(self, wf_id: str) -> dict:
        """Execute a workflow by running its steps in sequence."""
        wf = self._workflows.get(wf_id)
        if not wf:
            return {"status": "error", "error": "Workflow not found"}

        from backend.agent.tools import ToolRegistry
        tools = ToolRegistry()

        start_time = time.time()
        results = []
        success = True

        for i, step in enumerate(wf.steps):
            tool_name = step.get("tool", "")
            params = step.get("params", {})
            step_name = step.get("name", f"Step {i+1}")

            try:
                result = await tools.execute(tool_name, params)
                results.append({
                    "step": step_name,
                    "tool": tool_name,
                    "status": result.get("status", "ok"),
                    "result": str(result.get("result", ""))[:500],
                })
                if result.get("status") == "error":
                    success = False
                    if step.get("stop_on_error", True):
                        break
            except Exception as e:
                results.append({
                    "step": step_name,
                    "tool": tool_name,
                    "status": "error",
                    "result": str(e),
                })
                success = False
                if step.get("stop_on_error", True):
                    break

        elapsed = round(time.time() - start_time, 2)

        # Update workflow stats
        wf.last_run = datetime.now(timezone.utc).isoformat()
        wf.run_count += 1
        self._save(wf)

        # Log execution
        execution = {
            "workflow_id": wf_id,
            "workflow_name": wf.name,
            "timestamp": wf.last_run,
            "duration_seconds": elapsed,
            "success": success,
            "steps_run": len(results),
            "steps_total": len(wf.steps),
            "results": results,
        }
        self._log_execution(execution)

        audit_log.log(
            audit_log.WORKFLOW_RUN,
            f"Ran workflow '{wf.name}': {'success' if success else 'failed'} ({elapsed}s)",
            {"id": wf_id, "success": success, "steps": len(results)},
        )

        return execution

    def _log_execution(self, execution: dict):
        with open(WORKFLOW_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(execution, default=str) + "\n")

    def get_execution_history(self, wf_id: str = None, limit: int = 20) -> list[dict]:
        """Get recent execution history."""
        if not WORKFLOW_LOG.exists():
            return []
        entries = []
        for line in WORKFLOW_LOG.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if wf_id and entry.get("workflow_id") != wf_id:
                    continue
                entries.append(entry)
            except json.JSONDecodeError:
                continue
        entries.reverse()
        return entries[:limit]

    # ── Templates ────────────────────────────────────────────────────

    @staticmethod
    def get_templates() -> list[dict]:
        """Pre-built workflow templates for common tasks."""
        return [
            {
                "name": "Daily Git Summary",
                "description": "Check git status of all repos and create a summary note",
                "steps": [
                    {"name": "List repos", "tool": "list_directory", "params": {"path": "data/repos", "recursive": False}},
                    {"name": "Save summary", "tool": "joplin_write", "params": {"title": "Daily Git Summary", "body": "{{results}}"}},
                ],
                "schedule": "daily",
                "tags": ["git", "reporting"],
            },
            {
                "name": "Code Quality Check",
                "description": "Run linting and tests on the current project",
                "steps": [
                    {"name": "Lint", "tool": "run_shell", "params": {"command": "python -m py_compile *.py"}},
                    {"name": "Tests", "tool": "run_shell", "params": {"command": "python -m pytest --tb=short"}},
                ],
                "schedule": "manual",
                "tags": ["quality", "testing"],
            },
            {
                "name": "Research & Document",
                "description": "Search for a topic, synthesize findings, save to Joplin",
                "steps": [
                    {"name": "Search", "tool": "web_search", "params": {"query": "{{topic}}"}},
                    {"name": "Save note", "tool": "joplin_write", "params": {"title": "Research: {{topic}}", "body": "{{results}}"}},
                ],
                "schedule": "manual",
                "tags": ["research", "notes"],
            },
            {
                "name": "Backup Wiki to Joplin",
                "description": "Export all wiki articles to Joplin notes",
                "steps": [
                    {"name": "List wiki", "tool": "list_directory", "params": {"path": "data/wiki"}},
                ],
                "schedule": "weekly",
                "tags": ["backup", "sync"],
            },
        ]


workflow_engine = WorkflowEngine()
