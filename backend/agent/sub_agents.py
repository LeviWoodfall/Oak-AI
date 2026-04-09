"""
Sub-Agent Spawning — parallel workers for complex tasks.
Inspired by deer-flow: lead agent decomposes tasks, spawns sub-agents
with scoped context, runs them in parallel, and converges results.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from backend.agent.audit_log import audit_log

logger = logging.getLogger("oak.agent.sub_agents")


class SubAgentTask:
    """A scoped task for a sub-agent."""

    def __init__(self, task_id: str, name: str, instruction: str,
                 tools: list[str] = None, context: str = ""):
        self.task_id = task_id
        self.name = name
        self.instruction = instruction
        self.tools = tools or []
        self.context = context
        self.status = "pending"  # pending, running, completed, failed
        self.result = ""
        self.started_at = ""
        self.completed_at = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "instruction": self.instruction[:200],
            "tools": self.tools,
            "status": self.status,
            "result": self.result[:500],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class SubAgentSpawner:
    """Manages parallel sub-agent execution."""

    def __init__(self):
        self._active_tasks: dict[str, SubAgentTask] = {}

    async def spawn(self, tasks: list[dict], max_parallel: int = 3) -> list[dict]:
        """Spawn multiple sub-agents in parallel.
        Each task dict: {name, instruction, tools?, context?}
        Returns list of results."""

        sub_tasks = []
        for t in tasks:
            task = SubAgentTask(
                task_id=str(uuid.uuid4())[:8],
                name=t.get("name", "unnamed"),
                instruction=t.get("instruction", ""),
                tools=t.get("tools", []),
                context=t.get("context", ""),
            )
            sub_tasks.append(task)
            self._active_tasks[task.task_id] = task

        # Run in parallel with concurrency limit
        semaphore = asyncio.Semaphore(max_parallel)
        results = await asyncio.gather(
            *[self._run_sub_agent(task, semaphore) for task in sub_tasks],
            return_exceptions=True,
        )

        # Collect results
        output = []
        for task, result in zip(sub_tasks, results):
            if isinstance(result, Exception):
                task.status = "failed"
                task.result = str(result)
            output.append(task.to_dict())
            self._active_tasks.pop(task.task_id, None)

        audit_log.log(
            audit_log.TOOL_CALL,
            f"Sub-agent batch: {len(tasks)} tasks, {sum(1 for t in sub_tasks if t.status == 'completed')} succeeded",
            {"tasks": [t.name for t in sub_tasks]},
            source="sub_agent",
        )

        return output

    async def _run_sub_agent(self, task: SubAgentTask, semaphore: asyncio.Semaphore) -> str:
        """Run a single sub-agent with tool access."""
        async with semaphore:
            task.status = "running"
            task.started_at = datetime.now(timezone.utc).isoformat()

            from backend.agent.tools import ToolRegistry
            from backend.llm_service import llm_service

            tools = ToolRegistry()

            # Build a focused prompt for this sub-task
            prompt = f"""You are a focused sub-agent working on a specific task.
Complete this task and return ONLY the result.

Task: {task.name}
Instructions: {task.instruction}
{"Context: " + task.context if task.context else ""}

If you need to use tools, output: <tool_call>{{"name": "tool_name", "params": {{}}}}</tool_call>
Otherwise, just provide your answer directly."""

            try:
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

                # Check for tool calls in the response
                import re
                tool_pattern = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
                tool_calls = tool_pattern.findall(full_response)

                for tc_json in tool_calls:
                    try:
                        tc = json.loads(tc_json)
                        tool_name = tc.get("name", "")
                        params = tc.get("params", {})
                        if not task.tools or tool_name in task.tools:
                            result = await tools.execute(tool_name, params)
                            full_response += f"\n[Tool {tool_name} result: {result.get('result', '')[:300]}]"
                    except (json.JSONDecodeError, Exception):
                        pass

                task.status = "completed"
                task.result = full_response
                task.completed_at = datetime.now(timezone.utc).isoformat()
                return full_response

            except Exception as e:
                task.status = "failed"
                task.result = str(e)
                task.completed_at = datetime.now(timezone.utc).isoformat()
                raise

    def get_active(self) -> list[dict]:
        """Get currently running sub-agent tasks."""
        return [t.to_dict() for t in self._active_tasks.values()]


sub_agent_spawner = SubAgentSpawner()
