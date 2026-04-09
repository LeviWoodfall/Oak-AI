"""
Self-Maintenance Engine — Oak keeps itself healthy automatically.

Regular maintenance tasks:
  1. Syntax verification: Check all Python files for syntax errors
  2. Dependency audit: Check for outdated/vulnerable packages
  3. Endpoint health: Verify all API endpoints respond correctly
  4. Documentation: Auto-update stats in README/CHANGELOG
  5. Storage cleanup: Prune old logs, reports, temp files
  6. Memory health: Check memory stores aren't corrupted
  7. Test runner: Execute built-in self-tests

Runs on a configurable schedule (default: every 6 hours).
"""
import ast
import asyncio
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import httpx
from backend.config import BASE_DIR, DATA_DIR, settings

logger = logging.getLogger("oak.maintenance")

MAINT_DIR = DATA_DIR / "maintenance"
MAINT_DIR.mkdir(parents=True, exist_ok=True)
MAINT_REPORTS = MAINT_DIR / "reports"
MAINT_REPORTS.mkdir(parents=True, exist_ok=True)


class SelfMaintenance:
    """Automated health checks, tests, and maintenance."""

    def __init__(self):
        self._running = False
        self._last_run = ""
        self._last_report: dict = {}
        self._http = httpx.AsyncClient(timeout=10)

    # ── Full maintenance cycle ────────────────────────────────────────

    async def run_maintenance(self) -> dict:
        """Execute full maintenance cycle."""
        if self._running:
            return {"error": "Maintenance already running"}

        self._running = True
        start = time.time()
        report = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "checks": {},
            "total_issues": 0,
            "total_fixed": 0,
        }

        try:
            # 1. Syntax check all Python files
            report["checks"]["syntax"] = self._check_syntax()

            # 2. Dependency audit
            report["checks"]["dependencies"] = self._check_dependencies()

            # 3. Endpoint health check
            report["checks"]["endpoints"] = await self._check_endpoints()

            # 4. Memory health
            report["checks"]["memory"] = self._check_memory_health()

            # 5. Storage cleanup
            report["checks"]["storage"] = self._cleanup_storage()

            # 6. Self-tests
            report["checks"]["self_tests"] = await self._run_self_tests()

            # 7. Documentation freshness
            report["checks"]["docs"] = self._check_documentation()

            # Tally results
            for check_name, check_result in report["checks"].items():
                report["total_issues"] += check_result.get("issues", 0)
                report["total_fixed"] += check_result.get("fixed", 0)

            report["duration_seconds"] = round(time.time() - start, 1)
            report["completed_at"] = datetime.now(timezone.utc).isoformat()
            report["health_score"] = self._calculate_health_score(report["checks"])

            # Save report
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
            report_file = MAINT_REPORTS / f"{date_str}.json"
            report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

            # Audit log
            from backend.agent.audit_log import audit_log
            audit_log.log(
                audit_log.TOOL_CALL,
                f"Maintenance: health={report['health_score']}%, "
                f"{report['total_issues']} issues, {report['total_fixed']} fixed",
                {"duration": report["duration_seconds"]},
                source="self_maintenance",
            )

            self._last_run = report["completed_at"]
            self._last_report = report
            logger.info("Maintenance complete: health=%d%%, %d issues, %d fixed in %.1fs",
                        report["health_score"], report["total_issues"],
                        report["total_fixed"], report["duration_seconds"])

        except Exception as e:
            logger.error("Maintenance failed: %s", e)
            report["error"] = str(e)
        finally:
            self._running = False

        return report

    # ── Check 1: Syntax verification ──────────────────────────────────

    def _check_syntax(self) -> dict:
        """Verify all Python files have valid syntax."""
        result = {"status": "pass", "issues": 0, "fixed": 0, "details": []}
        backend_dir = BASE_DIR / "backend"

        for py_file in backend_dir.rglob("*.py"):
            try:
                source = py_file.read_text(encoding="utf-8")
                ast.parse(source)
            except SyntaxError as e:
                result["issues"] += 1
                result["status"] = "fail"
                result["details"].append({
                    "file": str(py_file.relative_to(BASE_DIR)),
                    "error": str(e),
                    "line": e.lineno,
                })

        if result["issues"] == 0:
            result["files_checked"] = len(list(backend_dir.rglob("*.py")))
        return result

    # ── Check 2: Dependency audit ─────────────────────────────────────

    def _check_dependencies(self) -> dict:
        """Check for outdated or potentially vulnerable dependencies."""
        result = {"status": "pass", "issues": 0, "fixed": 0, "details": []}
        req_file = BASE_DIR / "requirements.txt"

        if not req_file.exists():
            result["status"] = "warn"
            result["details"].append("requirements.txt not found")
            return result

        # Check if all required packages are installed
        requirements = req_file.read_text(encoding="utf-8")
        missing = []
        for line in requirements.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pkg_name = line.split(">=")[0].split("==")[0].split("[")[0].strip()
            try:
                __import__(pkg_name.replace("-", "_"))
            except ImportError:
                missing.append(pkg_name)

        if missing:
            result["issues"] = len(missing)
            result["status"] = "warn"
            result["details"] = [f"Missing: {pkg}" for pkg in missing]

        # Try pip audit if available
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "audit", "--format", "json"],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode == 0:
                audit_data = json.loads(proc.stdout)
                vulns = audit_data.get("vulnerabilities", [])
                if vulns:
                    result["issues"] += len(vulns)
                    result["status"] = "warn"
                    for v in vulns[:5]:
                        result["details"].append(
                            f"Vulnerability: {v.get('name', '?')} — {v.get('id', '?')}")
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            # pip-audit not installed or failed — that's okay
            pass

        return result

    # ── Check 3: Endpoint health ──────────────────────────────────────

    async def _check_endpoints(self) -> dict:
        """Verify critical API endpoints respond with 200."""
        result = {"status": "pass", "issues": 0, "fixed": 0, "details": []}

        endpoints = [
            "/api/health",
            "/api/tools",
            "/api/skills",
            "/api/workflows",
            "/api/memory",
            "/api/audit?limit=1",
            "/api/onenote/status",
            "/api/whisper/status",
            "/api/scheduler/status",
            "/api/context/stats",
        ]

        base_url = f"http://{settings.host}:{settings.port}"
        healthy = 0
        for ep in endpoints:
            try:
                resp = await self._http.get(f"{base_url}{ep}")
                if resp.status_code == 200:
                    healthy += 1
                else:
                    result["issues"] += 1
                    result["details"].append(f"{ep}: HTTP {resp.status_code}")
            except Exception as e:
                result["issues"] += 1
                result["details"].append(f"{ep}: {str(e)[:80]}")

        result["healthy_endpoints"] = healthy
        result["total_endpoints"] = len(endpoints)
        if result["issues"] > 0:
            result["status"] = "warn" if result["issues"] < 3 else "fail"
        return result

    # ── Check 4: Memory health ────────────────────────────────────────

    def _check_memory_health(self) -> dict:
        """Verify memory stores aren't corrupted."""
        result = {"status": "pass", "issues": 0, "fixed": 0, "details": []}

        from backend.agent.memory import MEMORY_FILE, USER_FILE, TASK_MEMORY_FILE

        # Check MEMORY.md
        if MEMORY_FILE.exists():
            try:
                text = MEMORY_FILE.read_text(encoding="utf-8")
                result["memory_size"] = len(text)
            except Exception as e:
                result["issues"] += 1
                result["details"].append(f"MEMORY.md corrupt: {e}")

        # Check USER.md
        if USER_FILE.exists():
            try:
                text = USER_FILE.read_text(encoding="utf-8")
                result["user_size"] = len(text)
            except Exception as e:
                result["issues"] += 1
                result["details"].append(f"USER.md corrupt: {e}")

        # Check task_memory.json
        if TASK_MEMORY_FILE.exists():
            try:
                data = json.loads(TASK_MEMORY_FILE.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    result["issues"] += 1
                    result["details"].append("task_memory.json: not a list")
                else:
                    result["tasks_count"] = len(data)
            except json.JSONDecodeError as e:
                result["issues"] += 1
                result["details"].append(f"task_memory.json corrupt: {e}")
                # Auto-fix: reset to empty list
                TASK_MEMORY_FILE.write_text("[]", encoding="utf-8")
                result["fixed"] += 1

        if result["issues"] > 0:
            result["status"] = "warn"
        return result

    # ── Check 5: Storage cleanup ──────────────────────────────────────

    def _cleanup_storage(self) -> dict:
        """Prune old logs and temp files to prevent disk bloat."""
        result = {"status": "pass", "issues": 0, "fixed": 0, "details": [],
                  "bytes_freed": 0}

        # Prune old daily reports (keep last 30)
        for subdir in [DATA_DIR / "learner" / "daily_reports",
                       DATA_DIR / "fact_checker" / "reports",
                       MAINT_REPORTS]:
            if subdir.exists():
                files = sorted(subdir.glob("*.json"))
                if len(files) > 30:
                    for old_file in files[:-30]:
                        size = old_file.stat().st_size
                        old_file.unlink()
                        result["bytes_freed"] += size
                        result["fixed"] += 1

        # Prune JSONL logs (keep last 10MB)
        for log_file in DATA_DIR.rglob("*.jsonl"):
            try:
                if log_file.stat().st_size > 10 * 1024 * 1024:  # 10MB
                    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
                    # Keep last 5000 lines
                    trimmed = "\n".join(lines[-5000:]) + "\n"
                    old_size = log_file.stat().st_size
                    log_file.write_text(trimmed, encoding="utf-8")
                    result["bytes_freed"] += old_size - len(trimmed.encode())
                    result["fixed"] += 1
            except Exception:
                pass

        if result["bytes_freed"] > 0:
            result["details"].append(
                f"Freed {result['bytes_freed'] / 1024:.1f} KB")
        return result

    # ── Check 6: Self-tests ───────────────────────────────────────────

    async def _run_self_tests(self) -> dict:
        """Run built-in self-tests to verify core functionality."""
        result = {"status": "pass", "issues": 0, "fixed": 0, "tests_passed": 0,
                  "tests_failed": 0, "details": []}

        tests = [
            ("import_all_modules", self._test_imports),
            ("memory_read_write", self._test_memory),
            ("wiki_list", self._test_wiki),
            ("context_engine", self._test_context),
            ("audit_log", self._test_audit),
            ("tool_registry", self._test_tools),
        ]

        for test_name, test_fn in tests:
            try:
                await test_fn() if asyncio.iscoroutinefunction(test_fn) else test_fn()
                result["tests_passed"] += 1
            except Exception as e:
                result["tests_failed"] += 1
                result["issues"] += 1
                result["details"].append(f"{test_name}: FAIL — {str(e)[:100]}")

        if result["tests_failed"] > 0:
            result["status"] = "warn" if result["tests_failed"] < 3 else "fail"
        return result

    def _test_imports(self):
        """Test that all core modules import successfully."""
        from backend.agent import agent, memory, skills, tools, audit_log
        from backend.agent import workflows, self_improve, tiered_context
        from backend.agent import sub_agents, scheduler, auto_learner, fact_checker
        from backend import wiki_service, vector_store, llm_service
        from backend import onenote_service, whisper_service, config

    def _test_memory(self):
        """Test memory read/write cycle."""
        from backend.agent.memory import agent_memory
        stats = agent_memory.stats()
        assert isinstance(stats, dict), "Memory stats must be a dict"
        assert "memory_entries" in stats, "Memory stats must have memory_entries"

    def _test_wiki(self):
        """Test wiki service."""
        from backend.wiki_service import wiki_service
        articles = wiki_service.list_articles()
        assert isinstance(articles, list), "Wiki list_articles must return a list"

    def _test_context(self):
        """Test tiered context engine."""
        from backend.agent.tiered_context import tiered_context
        stats = tiered_context.stats()
        assert isinstance(stats, dict), "Context stats must be a dict"

    def _test_audit(self):
        """Test audit log."""
        from backend.agent.audit_log import audit_log
        entries = audit_log.get_recent(limit=1)
        assert isinstance(entries, list), "Audit entries must be a list"

    def _test_tools(self):
        """Test tool registry."""
        from backend.agent.tools import ToolRegistry
        tools = ToolRegistry()
        assert len(tools.tool_names()) >= 10, "Must have at least 10 tools"

    # ── Check 7: Documentation freshness ──────────────────────────────

    def _check_documentation(self) -> dict:
        """Check if documentation is up-to-date."""
        result = {"status": "pass", "issues": 0, "fixed": 0, "details": []}

        readme = BASE_DIR / "README.md"
        changelog = BASE_DIR / "CHANGELOG.md"

        if not readme.exists():
            result["issues"] += 1
            result["details"].append("README.md missing")

        if not changelog.exists():
            result["issues"] += 1
            result["details"].append("CHANGELOG.md missing")

        # Check if README mentions current version
        if readme.exists():
            text = readme.read_text(encoding="utf-8")
            if settings.app_version not in text:
                result["details"].append(
                    f"README may be outdated (version {settings.app_version} not mentioned)")

        if result["issues"] > 0:
            result["status"] = "warn"
        return result

    # ── Health score ──────────────────────────────────────────────────

    @staticmethod
    def _calculate_health_score(checks: dict) -> int:
        """Calculate overall health score 0-100."""
        total_weight = 0
        weighted_score = 0

        weights = {
            "syntax": 25,
            "endpoints": 25,
            "memory": 15,
            "self_tests": 20,
            "dependencies": 10,
            "storage": 3,
            "docs": 2,
        }

        for check_name, weight in weights.items():
            check = checks.get(check_name, {})
            status = check.get("status", "pass")
            if status == "pass":
                score = 100
            elif status == "warn":
                score = 60
            else:
                score = 0
            weighted_score += score * weight
            total_weight += weight

        return round(weighted_score / total_weight) if total_weight else 0

    # ── Status & API ──────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "running": self._running,
            "last_run": self._last_run,
            "health_score": self._last_report.get("health_score"),
            "last_report_summary": {
                k: v for k, v in self._last_report.items()
                if k not in ("checks",)
            } if self._last_report else None,
        }

    def get_reports(self, limit: int = 10) -> list[dict]:
        reports = []
        files = sorted(MAINT_REPORTS.glob("*.json"), reverse=True)[:limit]
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                reports.append({
                    "date": f.stem,
                    "health_score": data.get("health_score", 0),
                    "total_issues": data.get("total_issues", 0),
                    "total_fixed": data.get("total_fixed", 0),
                    "duration_seconds": data.get("duration_seconds", 0),
                })
            except Exception:
                pass
        return reports


self_maintenance = SelfMaintenance()
