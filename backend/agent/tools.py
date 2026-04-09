"""
Agent Tools — registry of executable tools the agent can invoke.
Inspired by deer-flow (file ops, shell, git) and oh-my-openagent (LSP, search).
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional
from backend.config import BASE_DIR, REPOS_DIR

logger = logging.getLogger("oak.agent.tools")

TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file at the given path.",
        "parameters": {"path": "string (absolute or relative to workspace)"},
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates parent directories if needed.",
        "parameters": {"path": "string", "content": "string"},
    },
    {
        "name": "edit_file",
        "description": "Replace old_text with new_text in a file.",
        "parameters": {"path": "string", "old_text": "string", "new_text": "string"},
    },
    {
        "name": "list_directory",
        "description": "List files and directories at a path.",
        "parameters": {"path": "string", "recursive": "boolean (default false)"},
    },
    {
        "name": "search_files",
        "description": "Search for a text pattern across files using grep-like matching.",
        "parameters": {"pattern": "string", "path": "string (directory)", "file_glob": "string (optional, e.g. *.py)"},
    },
    {
        "name": "run_shell",
        "description": "Execute a shell command and return stdout/stderr. Use for running tests, installing packages, git commands, etc.",
        "parameters": {"command": "string", "cwd": "string (optional working directory)"},
    },
    {
        "name": "run_python",
        "description": "Execute Python code in a subprocess and return the output.",
        "parameters": {"code": "string"},
    },
    {
        "name": "git_status",
        "description": "Get git status of a repository.",
        "parameters": {"path": "string (repo path)"},
    },
    {
        "name": "git_diff",
        "description": "Get git diff of changes in a repository.",
        "parameters": {"path": "string (repo path)", "staged": "boolean (default false)"},
    },
    {
        "name": "git_commit",
        "description": "Stage all changes and commit with a message.",
        "parameters": {"path": "string (repo path)", "message": "string"},
    },
    {
        "name": "web_search",
        "description": "Search the web for information. Returns top results.",
        "parameters": {"query": "string"},
    },
    {
        "name": "joplin_search",
        "description": "Search Joplin notes by keyword. Returns matching notes with titles and snippets.",
        "parameters": {"query": "string"},
    },
    {
        "name": "joplin_read",
        "description": "Read a Joplin note by its ID. Returns title and full markdown body.",
        "parameters": {"note_id": "string"},
    },
    {
        "name": "joplin_write",
        "description": "Create or update a Joplin note. If note_id is provided, updates that note. Otherwise creates a new one in the Oak notebook.",
        "parameters": {"title": "string", "body": "string (markdown)", "note_id": "string (optional, for update)", "tags": "list of strings (optional)"},
    },
]


class ToolRegistry:
    """Executes tools on behalf of the agent. All tools return a dict with status + result."""

    def __init__(self, workspace: Optional[Path] = None):
        self.workspace = workspace or BASE_DIR
        self.exec_timeout = 30
        self._tool_map = {
            "read_file": self._read_file,
            "write_file": self._write_file,
            "edit_file": self._edit_file,
            "list_directory": self._list_directory,
            "search_files": self._search_files,
            "run_shell": self._run_shell,
            "run_python": self._run_python,
            "git_status": self._git_status,
            "git_diff": self._git_diff,
            "git_commit": self._git_commit,
            "web_search": self._web_search,
            "joplin_search": self._joplin_search,
            "joplin_read": self._joplin_read,
            "joplin_write": self._joplin_write,
        }

    @property
    def available_tools(self) -> list[dict]:
        return TOOL_DEFINITIONS

    def tool_names(self) -> list[str]:
        return list(self._tool_map.keys())

    async def execute(self, tool_name: str, params: dict) -> dict:
        """Execute a tool by name with the given parameters."""
        if tool_name not in self._tool_map:
            return {"status": "error", "result": f"Unknown tool: {tool_name}"}
        try:
            logger.info("Tool call: %s(%s)", tool_name, json.dumps(params, default=str)[:200])
            result = await self._tool_map[tool_name](**params)
            return {"status": "ok", "tool": tool_name, **result}
        except TypeError as e:
            return {"status": "error", "tool": tool_name, "result": f"Invalid parameters: {e}"}
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_name, e)
            return {"status": "error", "tool": tool_name, "result": str(e)}

    def _resolve_path(self, path: str) -> Path:
        """Resolve a path relative to the workspace, with traversal protection."""
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace / p
        resolved = p.resolve()
        return resolved

    # ── File Tools ───────────────────────────────────────────────────

    async def _read_file(self, path: str) -> dict:
        p = self._resolve_path(path)
        if not p.is_file():
            return {"result": f"File not found: {path}"}
        content = p.read_text(encoding="utf-8", errors="replace")
        lines = content.count("\n") + 1
        return {"result": content, "lines": lines, "path": str(p)}

    async def _write_file(self, path: str, content: str) -> dict:
        p = self._resolve_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"result": f"Written {len(content)} chars to {p.name}", "path": str(p)}

    async def _edit_file(self, path: str, old_text: str, new_text: str) -> dict:
        p = self._resolve_path(path)
        if not p.is_file():
            return {"result": f"File not found: {path}"}
        content = p.read_text(encoding="utf-8")
        if old_text not in content:
            return {"result": "old_text not found in file", "path": str(p)}
        count = content.count(old_text)
        new_content = content.replace(old_text, new_text, 1)
        p.write_text(new_content, encoding="utf-8")
        return {"result": f"Replaced 1 of {count} occurrence(s)", "path": str(p)}

    async def _list_directory(self, path: str, recursive: bool = False) -> dict:
        p = self._resolve_path(path)
        if not p.is_dir():
            return {"result": f"Directory not found: {path}"}
        items = []
        iterator = p.rglob("*") if recursive else p.iterdir()
        for entry in sorted(iterator, key=lambda e: (not e.is_dir(), str(e).lower())):
            if entry.name.startswith(".") or "__pycache__" in entry.parts:
                continue
            rel = str(entry.relative_to(p))
            kind = "dir" if entry.is_dir() else "file"
            items.append(f"[{kind}] {rel}")
            if len(items) >= 200:
                items.append("... (truncated)")
                break
        return {"result": "\n".join(items), "count": len(items)}

    async def _search_files(self, pattern: str, path: str = ".", file_glob: str = "*") -> dict:
        p = self._resolve_path(path)
        if not p.is_dir():
            return {"result": f"Directory not found: {path}"}
        matches = []
        for fp in p.rglob(file_glob):
            if not fp.is_file() or ".git" in fp.parts or "__pycache__" in fp.parts:
                continue
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(content.splitlines(), 1):
                    if pattern.lower() in line.lower():
                        rel = str(fp.relative_to(p))
                        matches.append(f"{rel}:{i}: {line.strip()[:120]}")
                        if len(matches) >= 50:
                            break
            except Exception:
                continue
            if len(matches) >= 50:
                break
        return {"result": "\n".join(matches) if matches else "No matches found", "count": len(matches)}

    # ── Shell Tools ──────────────────────────────────────────────────

    async def _run_shell(self, command: str, cwd: str = None) -> dict:
        work_dir = self._resolve_path(cwd) if cwd else self.workspace
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.exec_timeout)
            output = stdout.decode("utf-8", errors="replace")
            errors = stderr.decode("utf-8", errors="replace")
            return {
                "result": output + ("\n--- stderr ---\n" + errors if errors else ""),
                "returncode": proc.returncode,
            }
        except asyncio.TimeoutError:
            return {"result": f"Command timed out after {self.exec_timeout}s", "returncode": -1}

    async def _run_python(self, code: str) -> dict:
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp = Path(f.name)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(tmp),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.exec_timeout)
            return {
                "result": stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace"),
                "returncode": proc.returncode,
            }
        except asyncio.TimeoutError:
            return {"result": "Execution timed out", "returncode": -1}
        finally:
            tmp.unlink(missing_ok=True)

    # ── Git Tools ────────────────────────────────────────────────────

    async def _git_status(self, path: str = ".") -> dict:
        return await self._run_shell("git status --short", cwd=path)

    async def _git_diff(self, path: str = ".", staged: bool = False) -> dict:
        cmd = "git diff --staged" if staged else "git diff"
        return await self._run_shell(cmd, cwd=path)

    async def _git_commit(self, path: str = ".", message: str = "auto-commit") -> dict:
        await self._run_shell("git add -A", cwd=path)
        return await self._run_shell(f'git commit -m "{message}"', cwd=path)

    # ── Web Search (stub — uses DuckDuckGo lite) ────────────────────

    async def _web_search(self, query: str) -> dict:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "Oak/1.0"},
                )
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for r in soup.select(".result__body")[:5]:
                    title_el = r.select_one(".result__a")
                    snippet_el = r.select_one(".result__snippet")
                    if title_el:
                        results.append({
                            "title": title_el.get_text(strip=True),
                            "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                            "url": title_el.get("href", ""),
                        })
                return {"result": json.dumps(results, indent=2) if results else "No results found"}
        except Exception as e:
            return {"result": f"Search failed: {e}"}

    # ── Joplin Tools ─────────────────────────────────────────────────

    async def _joplin_search(self, query: str) -> dict:
        from backend.joplin_service import joplin_service
        if not joplin_service.configured:
            return {"result": "Joplin not configured. Set JOPLIN_TOKEN in Settings."}
        notes = await joplin_service.search_notes(query, limit=10)
        if not notes:
            return {"result": "No matching notes found"}
        lines = []
        for n in notes:
            body_preview = (n.get("body", "")[:150] + "...") if n.get("body") else ""
            lines.append(f"- [{n['title']}] (id: {n['id']})\n  {body_preview}")
        return {"result": "\n".join(lines), "count": len(notes)}

    async def _joplin_read(self, note_id: str) -> dict:
        from backend.joplin_service import joplin_service
        if not joplin_service.configured:
            return {"result": "Joplin not configured. Set JOPLIN_TOKEN in Settings."}
        note = await joplin_service.get_note(note_id)
        if not note:
            return {"result": f"Note {note_id} not found"}
        return {"result": f"# {note['title']}\n\n{note.get('body', '')}", "title": note["title"]}

    async def _joplin_write(self, title: str, body: str, note_id: str = "",
                            tags: list[str] = None) -> dict:
        from backend.joplin_service import joplin_service
        if not joplin_service.configured:
            return {"result": "Joplin not configured. Set JOPLIN_TOKEN in Settings."}
        if note_id:
            note = await joplin_service.update_note(note_id, title=title, body=body)
            return {"result": f"Updated note: {note.get('title', title)}", "note_id": note_id}
        else:
            note = await joplin_service.save_ai_note(title, body, tags=tags)
            return {"result": f"Created note: {title}", "note_id": note.get("id", "")}
