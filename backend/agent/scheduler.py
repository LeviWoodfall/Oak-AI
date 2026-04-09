"""
Oak Scheduler — background async loop for all autonomous operations.

Manages three cadences:
  1. Workflows:    user-defined scheduled workflows (daily/hourly/every Xm)
  2. Auto-Learner: daily discovery + processing of trending repos
  3. Fact Checker:  runs TWICE as often as learning (every 12h)
  4. Maintenance:   self-tests, vuln checks, cleanup (every 6h)
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("oak.agent.scheduler")

# Autonomous task intervals (seconds)
LEARN_INTERVAL = 86400       # Daily (24h)
FACT_CHECK_INTERVAL = 43200  # Twice daily (12h) — 2x learning frequency
MAINTENANCE_INTERVAL = 21600 # Every 6 hours


def _parse_interval_seconds(schedule: str) -> Optional[int]:
    """Parse schedule string into seconds. Returns None for manual/unsupported."""
    s = schedule.strip().lower()
    if s in ("manual", ""):
        return None
    if s == "daily":
        return 86400
    if s == "weekly":
        return 604800
    if s == "hourly" or s == "every 1h":
        return 3600
    if s.startswith("every "):
        part = s[6:].strip()
        if part.endswith("m"):
            try:
                return int(part[:-1]) * 60
            except ValueError:
                pass
        if part.endswith("h"):
            try:
                return int(part[:-1]) * 3600
            except ValueError:
                pass
        if part.endswith("s"):
            try:
                return int(part[:-1])
            except ValueError:
                pass
    return None


class WorkflowScheduler:
    """Background task that runs scheduled workflows AND autonomous systems."""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._check_interval = 60  # check every 60 seconds
        self._last_run: dict[str, float] = {}
        # Autonomous system tracking
        self._last_learn = 0.0
        self._last_fact_check = 0.0
        self._last_maintenance = 0.0
        self._autonomous_enabled = True

    @property
    def running(self) -> bool:
        return self._running

    def start(self):
        """Start the scheduler background loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Scheduler started (workflows + autonomous learning + fact-check + maintenance)")

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Scheduler stopped")

    def set_autonomous(self, enabled: bool):
        """Enable/disable autonomous learning, fact-checking, and maintenance."""
        self._autonomous_enabled = enabled
        logger.info("Autonomous systems %s", "enabled" if enabled else "disabled")

    async def _loop(self):
        """Main scheduler loop."""
        while self._running:
            try:
                await self._check_and_run()
                if self._autonomous_enabled:
                    await self._check_autonomous()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Scheduler error: %s", e)
            await asyncio.sleep(self._check_interval)

    async def _check_and_run(self):
        """Check all workflows and run any that are due."""
        from backend.agent.workflows import workflow_engine

        now = time.time()
        for wf_dict in workflow_engine.list_all():
            wf_id = wf_dict["id"]
            schedule = wf_dict.get("schedule", "")
            enabled = wf_dict.get("enabled", True)

            if not enabled:
                continue

            interval = _parse_interval_seconds(schedule)
            if interval is None:
                continue

            last = self._last_run.get(wf_id, 0)
            if now - last >= interval:
                logger.info("Scheduler: running workflow '%s'", wf_dict["name"])
                try:
                    result = await workflow_engine.run(wf_id)
                    self._last_run[wf_id] = now
                    logger.info("Scheduler: '%s' completed — %s",
                                wf_dict["name"],
                                "success" if result.get("success") else "failed")
                except Exception as e:
                    logger.error("Scheduler: '%s' failed — %s", wf_dict["name"], e)
                    self._last_run[wf_id] = now

    async def _check_autonomous(self):
        """Check and run autonomous systems on their schedules."""
        now = time.time()

        # Auto-Learner: daily
        if now - self._last_learn >= LEARN_INTERVAL:
            logger.info("Scheduler: starting daily learning cycle")
            try:
                from backend.agent.auto_learner import auto_learner
                report = await auto_learner.run_daily()
                self._last_learn = now
                logger.info("Scheduler: learning complete — %d repos, %d articles",
                            report.get("repos_processed", 0),
                            report.get("articles_created", 0))
            except Exception as e:
                logger.error("Scheduler: learning failed — %s", e)
                self._last_learn = now  # Don't retry immediately

        # Fact Checker: twice daily (2x learning frequency)
        if now - self._last_fact_check >= FACT_CHECK_INTERVAL:
            logger.info("Scheduler: starting fact-check cycle")
            try:
                from backend.agent.fact_checker import fact_checker
                report = await fact_checker.run_verification()
                self._last_fact_check = now
                logger.info("Scheduler: fact-check complete — %d issues, %d fixed",
                            report.get("issues_found", 0),
                            report.get("issues_fixed", 0))
            except Exception as e:
                logger.error("Scheduler: fact-check failed — %s", e)
                self._last_fact_check = now

        # Self-Maintenance: every 6 hours
        if now - self._last_maintenance >= MAINTENANCE_INTERVAL:
            logger.info("Scheduler: starting maintenance cycle")
            try:
                from backend.agent.self_maintenance import self_maintenance
                report = await self_maintenance.run_maintenance()
                self._last_maintenance = now
                logger.info("Scheduler: maintenance complete — health=%d%%",
                            report.get("health_score", 0))
            except Exception as e:
                logger.error("Scheduler: maintenance failed — %s", e)
                self._last_maintenance = now

    def status(self) -> dict:
        now = time.time()
        return {
            "running": self._running,
            "autonomous_enabled": self._autonomous_enabled,
            "check_interval": self._check_interval,
            "workflow_last_runs": {
                k: datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
                for k, v in self._last_run.items()
            },
            "autonomous": {
                "learn": {
                    "interval_hours": LEARN_INTERVAL / 3600,
                    "last_run": datetime.fromtimestamp(self._last_learn, tz=timezone.utc).isoformat() if self._last_learn else None,
                    "next_in_minutes": max(0, round((LEARN_INTERVAL - (now - self._last_learn)) / 60)) if self._last_learn else 0,
                },
                "fact_check": {
                    "interval_hours": FACT_CHECK_INTERVAL / 3600,
                    "last_run": datetime.fromtimestamp(self._last_fact_check, tz=timezone.utc).isoformat() if self._last_fact_check else None,
                    "next_in_minutes": max(0, round((FACT_CHECK_INTERVAL - (now - self._last_fact_check)) / 60)) if self._last_fact_check else 0,
                },
                "maintenance": {
                    "interval_hours": MAINTENANCE_INTERVAL / 3600,
                    "last_run": datetime.fromtimestamp(self._last_maintenance, tz=timezone.utc).isoformat() if self._last_maintenance else None,
                    "next_in_minutes": max(0, round((MAINTENANCE_INTERVAL - (now - self._last_maintenance)) / 60)) if self._last_maintenance else 0,
                },
            },
        }


workflow_scheduler = WorkflowScheduler()
