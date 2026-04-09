"""
Agent Core — the main agentic loop that plans, executes tools, and verifies.
Inspired by deer-flow (sub-agents, context engineering), hermes-agent (self-improving),
and autoresearch (edit → run → measure → iterate).

The agent interprets user requests, decides which tools/skills to use,
executes them, and returns structured results with streaming.
"""
import json
import logging
import re
from typing import AsyncGenerator, Optional
from backend.config import settings
from backend.llm_service import llm_service
from backend.agent.tools import ToolRegistry
from backend.agent.memory import agent_memory
from backend.agent.skills import skill_loader
from backend.agent.skill_library import skill_library

logger = logging.getLogger("oak.agent")

TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)


class AgentState:
    """Tracks state for one agent turn."""

    def __init__(self):
        self.tool_calls: list[dict] = []
        self.tool_results: list[dict] = []
        self.active_skill: Optional[str] = None
        self.active_skill_id: Optional[str] = None  # skill_library ID for reflect-write
        self.plan: list[str] = []
        self.status: str = "thinking"
        self.had_errors: bool = False
        self.last_error: str = ""


class CodingAgent:
    """
    The Oak AI coding agent.
    Runs an agentic loop: think → plan → use tools → verify → respond.
    """

    def __init__(self):
        self.tools = ToolRegistry()
        self.max_tool_rounds = 8

    def _build_system_prompt(self, active_skill_prompt: str = "") -> str:
        """Build the full system prompt with memory, rules, and optional skill."""
        memory_ctx = agent_memory.build_context()
        tools_desc = self._format_tool_descriptions()

        prompt = f"""You are Oak, a self-improving local AI coding agent specialised in Python.
You are not just a chatbot — you are an autonomous agent that can read files, write code,
run commands, search the web, and execute multi-step tasks.

## Core Principles (always follow)
1. **Think Before Coding** — State assumptions explicitly. If uncertain, ask. Present tradeoffs.
2. **Simplicity First** — Minimum code that solves the problem. No speculative features.
3. **Surgical Changes** — Touch only what you must. Match existing style.
4. **Goal-Driven Execution** — Define success criteria. Loop until verified.

## Tools
You have access to these tools. To call a tool, output:
<tool_call>{{"name": "tool_name", "params": {{"key": "value"}}}}</tool_call>

You may call multiple tools in sequence. After each tool call, you will receive the result
and can decide to call more tools or respond to the user.

{tools_desc}

## When to use tools
- User asks to read/write/edit files → use file tools
- User asks to run code or tests → use run_python or run_shell
- User asks about project structure → use list_directory + search_files
- User asks to commit changes → use git tools
- User asks for current info → use web_search

## Memory
{memory_ctx if memory_ctx else "No memory context yet."}

{active_skill_prompt}

## Response Format
- Use markdown formatting with code blocks
- When you use tools, briefly explain what you're doing
- After completing a task, summarize what was done
- If you learn something about the user's preferences, note it"""

        return prompt

    def _format_tool_descriptions(self) -> str:
        lines = []
        for tool in self.tools.available_tools:
            params = ", ".join(f"{k}: {v}" for k, v in tool["parameters"].items())
            lines.append(f"- **{tool['name']}**({params}): {tool['description']}")
        return "\n".join(lines)

    async def chat(
        self,
        messages: list[dict],
        conversation_id: str = "",
        use_rag: bool = True,
        temperature: float = 0.7,
    ) -> AsyncGenerator[dict, None]:
        """
        Run the agent loop. Yields events:
        - {"type": "status", "status": "thinking|calling_tool|responding"}
        - {"type": "tool_call", "tool": "name", "params": {...}}
        - {"type": "tool_result", "tool": "name", "result": "..."}
        - {"type": "token", "content": "..."}
        - {"type": "done", "tool_calls": [...]}
        - {"type": "memory", "action": "...", "data": {...}}
        """
        state = AgentState()

        # Check for slash command skills
        last_user_msg = messages[-1]["content"] if messages else ""
        skill_prompt = ""

        # 1. Try skill_library hybrid routing first (Memento pattern)
        if last_user_msg.startswith("/"):
            trigger = last_user_msg.split()[0]
            lib_skill = skill_library.get_by_trigger(trigger)
            if lib_skill:
                state.active_skill = lib_skill.name
                state.active_skill_id = lib_skill.skill_id
                skill_prompt = lib_skill.to_prompt()
                yield {"type": "status", "status": f"Activating skill: {lib_skill.name} (utility: {lib_skill.utility})"}

        # 2. Hybrid route: semantic + keyword + utility scoring
        if not skill_prompt:
            routed = skill_library.route(last_user_msg, max_results=2)
            if routed:
                state.active_skill_id = routed[0].skill_id
                state.active_skill = routed[0].name
                skill_prompt = "\n\n".join(s.to_prompt() for s in routed)

        # 3. Fallback to legacy skill_loader if library is empty
        if not skill_prompt:
            if last_user_msg.startswith("/"):
                trigger = last_user_msg.split()[0]
                skill = skill_loader.find_by_trigger(trigger)
                if skill:
                    state.active_skill = skill.slug
                    skill_prompt = skill.to_prompt()
            if not skill_prompt:
                relevant_skills = skill_loader.find_relevant(last_user_msg, max_skills=2)
                if relevant_skills:
                    skill_prompt = "\n\n".join(
                        f"## Available Skill: {s.title}\n{s.description}"
                        for s in relevant_skills
                    )

        # RAG context
        context_docs = []
        if use_rag:
            try:
                from backend.vector_store import vector_store
                context_docs = vector_store.search_all(last_user_msg, n_results=3)
            except Exception:
                pass

        # Build system prompt
        system_prompt = self._build_system_prompt(skill_prompt)
        if context_docs:
            system_prompt += "\n\n--- Relevant Knowledge ---\n"
            for i, doc in enumerate(context_docs, 1):
                system_prompt += f"\n[{i}] {doc}\n"

        # Agent loop — think, call tools, respond
        for round_num in range(self.max_tool_rounds + 1):
            yield {"type": "status", "status": "thinking" if round_num == 0 else "reasoning"}

            # Stream LLM response
            full_response = ""
            async for chunk in llm_service.chat(
                messages=messages,
                system_prompt=system_prompt,
                temperature=temperature,
            ):
                try:
                    data = json.loads(chunk)
                    if "message" in data and "content" in data["message"]:
                        token = data["message"]["content"]
                        full_response += token
                        yield {"type": "token", "content": token}
                    if data.get("done"):
                        break
                except json.JSONDecodeError:
                    pass

            # Check for tool calls in the response
            tool_calls = TOOL_CALL_PATTERN.findall(full_response)
            if not tool_calls:
                # No tool calls — agent is done responding
                break

            # Execute tool calls
            for tc_json in tool_calls:
                try:
                    tc = json.loads(tc_json)
                    tool_name = tc.get("name", "")
                    params = tc.get("params", {})

                    yield {"type": "tool_call", "tool": tool_name, "params": params}
                    yield {"type": "status", "status": f"Running {tool_name}..."}

                    result = await self.tools.execute(tool_name, params)
                    state.tool_calls.append({"name": tool_name, "params": params})
                    state.tool_results.append(result)

                    # Truncate large results for context
                    result_text = result.get("result", "")
                    if len(result_text) > 3000:
                        result_text = result_text[:3000] + "\n... (truncated)"

                    yield {"type": "tool_result", "tool": tool_name, "result": result_text[:500]}

                except (json.JSONDecodeError, Exception) as e:
                    error_result = f"Tool call failed: {e}"
                    yield {"type": "tool_result", "tool": "error", "result": error_result}
                    result_text = error_result
                    state.had_errors = True
                    state.last_error = str(e)

            # Add tool results to conversation for next round
            tool_summary = "\n".join(
                f"[Tool: {tc['name']}] Result:\n{r.get('result', '')[:2000]}"
                for tc, r in zip(state.tool_calls[-len(tool_calls):],
                                 state.tool_results[-len(tool_calls):])
            )
            messages.append({"role": "assistant", "content": full_response})
            messages.append({"role": "user", "content": f"Tool results:\n{tool_summary}\n\nContinue with the task. If done, provide your final response."})

        # Extract memory from conversation
        await self._extract_memory(last_user_msg, full_response, state)

        # Memento Reflect→Write loop: record skill execution & improve on failure
        if state.active_skill_id:
            success = not state.had_errors
            skill_library.record_execution(
                state.active_skill_id, success=success,
                task=last_user_msg[:200],
                error=state.last_error[:200] if state.last_error else "",
            )
            # If skill failed, attempt automatic improvement
            if not success:
                try:
                    improved = await skill_library.reflect_and_improve(
                        state.active_skill_id,
                        task=last_user_msg[:500],
                        error=state.last_error[:500],
                    )
                    if improved:
                        yield {"type": "status",
                               "status": f"Improved skill '{improved.name}' → v{improved.version}"}
                except Exception as e:
                    logger.warning("Skill improvement failed: %s", e)

        yield {
            "type": "done",
            "tool_calls": [tc for tc in state.tool_calls],
            "skill_used": state.active_skill,
            "skill_id": state.active_skill_id,
        }

    async def _extract_memory(self, user_msg: str, response: str, state: AgentState):
        """Extract facts and learnings from the conversation."""
        tools_used = [tc["name"] for tc in state.tool_calls]
        if tools_used:
            task_desc = user_msg[:100]
            agent_memory.record_task(
                task=task_desc,
                result=response[:200] if response else "no response",
                success=True,
                tools_used=tools_used,
            )


coding_agent = CodingAgent()
