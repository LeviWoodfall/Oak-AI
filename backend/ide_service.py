"""
IDE Service — Provides file operations for Oak's built-in IDE.
Allows Oak to read, write, and navigate its own codebase for self-improvement.
"""
import logging
from pathlib import Path
from typing import Optional, List, Dict
from backend.config import BASE_DIR

logger = logging.getLogger("oak.ide")


class IDEService:
    """Service for interacting with Oak's codebase through the IDE."""

    def __init__(self):
        self.codebase = BASE_DIR
        self.excluded_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv',
                             'env', 'dist', 'build', 'target', 'bin', 'obj', '.next',
                             '.vscode', '.idea', 'coverage', '.pytest_cache', '.mypy_cache',
                             'data', 'learner', 'skills', 'self_improvement_proposals'}

    def list_files(self, path: str = "", extensions: List[str] = None) -> List[Dict]:
        """List files in the codebase."""
        target_path = self.codebase / path if path else self.codebase
        files = []

        try:
            for item in target_path.rglob("*"):
                # Skip excluded directories
                if any(excluded in str(item) for excluded in self.excluded_dirs):
                    continue

                if item.is_file():
                    # Filter by extension if specified
                    if extensions:
                        if item.suffix.lower() not in [e.lower() for e in extensions]:
                            continue

                    files.append({
                        "path": str(item.relative_to(self.codebase)),
                        "name": item.name,
                        "extension": item.suffix,
                        "size": item.stat().st_size,
                    })
        except Exception as e:
            logger.error("Failed to list files: %s", e)

        return sorted(files, key=lambda f: f["path"])

    def read_file(self, path: str) -> Optional[str]:
        """Read a file from the codebase."""
        file_path = self.codebase / path
        try:
            return file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("Failed to read file %s: %s", path, e)
            return None

    def write_file(self, path: str, content: str) -> bool:
        """Write content to a file in the codebase."""
        file_path = self.codebase / path
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            logger.info("Wrote file: %s", path)
            return True
        except Exception as e:
            logger.error("Failed to write file %s: %s", path, e)
            return False

    def create_file(self, path: str, content: str = "") -> bool:
        """Create a new file in the codebase."""
        return self.write_file(path, content)

    def delete_file(self, path: str) -> bool:
        """Delete a file from the codebase."""
        file_path = self.codebase / path
        try:
            file_path.unlink()
            logger.info("Deleted file: %s", path)
            return True
        except Exception as e:
            logger.error("Failed to delete file %s: %s", path, e)
            return False

    def file_exists(self, path: str) -> bool:
        """Check if a file exists in the codebase."""
        return (self.codebase / path).exists()

    def get_file_stats(self, path: str) -> Optional[Dict]:
        """Get statistics for a file."""
        file_path = self.codebase / path
        try:
            stat = file_path.stat()
            return {
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "created": stat.st_ctime,
            }
        except Exception as e:
            logger.error("Failed to get stats for %s: %s", path, e)
            return None

    def search_files(self, query: str, extensions: List[str] = None) -> List[Dict]:
        """Search for files containing a query string."""
        results = []
        files = self.list_files(extensions=extensions)

        for file_info in files:
            content = self.read_file(file_info["path"])
            if content and query.lower() in content.lower():
                # Find context around the match
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    if query.lower() in line.lower():
                        context_start = max(0, i - 2)
                        context_end = min(len(lines), i + 3)
                        context = '\n'.join(lines[context_start:context_end])
                        results.append({
                            "path": file_info["path"],
                            "line": i + 1,
                            "context": context,
                        })
                        break  # Only report first match per file

        return results

    def apply_diff(self, path: str, old_text: str, new_text: str) -> bool:
        """Apply a diff to a file (replace old_text with new_text)."""
        content = self.read_file(path)
        if content is None:
            return False

        if old_text not in content:
            logger.warning("Old text not found in file %s", path)
            return False

        new_content = content.replace(old_text, new_text, 1)  # Replace only first occurrence
        return self.write_file(path, new_content)


ide_service = IDEService()
