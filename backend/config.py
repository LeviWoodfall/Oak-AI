"""
CodePilot configuration — auto-detects hardware and selects appropriate model.
"""
import os
import platform
import subprocess
import psutil
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field


def _detect_gpu() -> dict:
    """Detect GPU availability and VRAM."""
    gpu_info = {"available": False, "name": "", "vram_gb": 0}
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(",")
            gpu_info["available"] = True
            gpu_info["name"] = parts[0].strip()
            gpu_info["vram_gb"] = round(int(parts[1].strip()) / 1024, 1)
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    return gpu_info


def _recommend_model(ram_gb: float, gpu: dict) -> str:
    """Select the best model for the detected hardware."""
    # GPU with >=6GB VRAM can fully offload 7B
    if gpu["available"] and gpu["vram_gb"] >= 6:
        return "qwen2.5-coder:7b"
    # Plenty of RAM can run 7B on CPU (GPU assists with partial offload)
    if ram_gb >= 16:
        return "qwen2.5-coder:7b"
    if ram_gb >= 8:
        return "qwen2.5-coder:3b"
    return "qwen2.5-coder:1.5b"


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WIKI_DIR = DATA_DIR / "wiki"
REPOS_DIR = DATA_DIR / "repos"
CHROMA_DIR = DATA_DIR / "chroma"
CONVERSATIONS_DIR = DATA_DIR / "conversations"

for d in [DATA_DIR, WIKI_DIR, REPOS_DIR, CHROMA_DIR, CONVERSATIONS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


class HardwareProfile:
    """Snapshot of current system hardware."""

    def __init__(self):
        self.ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        self.cpu_count = psutil.cpu_count(logical=True)
        self.platform = platform.system()
        self.gpu = _detect_gpu()
        self.recommended_model = _recommend_model(self.ram_gb, self.gpu)

    def to_dict(self) -> dict:
        return {
            "ram_gb": self.ram_gb,
            "cpu_count": self.cpu_count,
            "platform": self.platform,
            "gpu": self.gpu,
            "recommended_model": self.recommended_model,
        }


HARDWARE = HardwareProfile()


class Settings(BaseSettings):
    """App settings — override via environment variables or .env file."""

    app_name: str = "Oak"
    app_version: str = "1.0.0"
    host: str = "127.0.0.1"
    port: int = 8800

    # Ollama
    ollama_host: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")
    default_model: str = Field(default=HARDWARE.recommended_model, alias="OAK_MODEL")

    # GitHub
    github_token: str = Field(default="", alias="GITHUB_TOKEN")

    # Embedding model (runs locally via sentence-transformers)
    embedding_model: str = "all-MiniLM-L6-v2"

    # Code execution
    code_exec_timeout: int = 30  # seconds
    code_exec_enabled: bool = True

    # Wiki
    wiki_auto_index: bool = True

    class Config:
        env_file = str(BASE_DIR / ".env")
        env_file_encoding = "utf-8"
        populate_by_name = True


settings = Settings()
