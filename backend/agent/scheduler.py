"""
Scheduled Workflow Runner — background async loop for cron-like execution.
Runs workflows on their defined schedule (daily, hourly, every Xm, etc.).
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("oak.agent.scheduler")


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
    """Background task that runs scheduled workflows."""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._check_interval = 60  # check every 60 seconds
        self._last_run: dict[str, float] = {}

    @property
    def running(self) -> bool:
        return self._running

    def start(self):
        """Start the scheduler background loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Workflow scheduler started")

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Workflow scheduler stopped")

    async def _loop(self):
        """Main scheduler loop — checks workflows and runs due ones."""
        while self._running:
            try:
                await self._check_and_run()
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

    def status(self) -> dict:
        return {
            "running": self._running,
            "check_interval": self._check_interval,
            "last_runs": {k: datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
                          for k, v in self._last_run.items()},
        }


workflow_scheduler = WorkflowScheduler()
