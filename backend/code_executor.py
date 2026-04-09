"""
Sandboxed Python code executor — runs user code in a subprocess with timeout.
"""
import asyncio
import logging
import sys
import tempfile
from pathlib import Path
from backend.config import settings

logger = logging.getLogger("oak.executor")


class CodeExecutor:
    """Execute Python code safely in a subprocess."""

    async def execute(self, code: str, timeout: int = None) -> dict:
        """
        Run Python code and return stdout, stderr, and return code.
        Code runs in a fresh subprocess with no access to the parent process.
        """
        timeout = timeout or settings.code_exec_timeout

        if not settings.code_exec_enabled:
            return {
                "stdout": "",
                "stderr": "Code execution is disabled in settings.",
                "returncode": -1,
                "timed_out": False,
            }

        # Write code to a temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = Path(f.name)

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(tmp_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(tmp_path.parent),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                return {
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "returncode": proc.returncode,
                    "timed_out": False,
                }
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "stdout": "",
                    "stderr": f"Execution timed out after {timeout}s",
                    "returncode": -1,
                    "timed_out": True,
                }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "timed_out": False,
            }
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass


code_executor = CodeExecutor()
