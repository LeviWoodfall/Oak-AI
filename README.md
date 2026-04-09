# Oak — Self-Improving AI Coding Agent

A fully local, self-improving AI agent that grows its knowledge and capabilities like an oak tree. It takes notes, generates wikis, auto-researches gaps, acquires skills from GitHub, builds its own skills, automates workflows, and serves as a full development IDE — all running on your hardware.

Like Claude Code, Windsurf, or Cursor — but local, self-improving, and yours.

## Features

### Self-Improving Agent
- **Agentic Loop** — Plan → use tools → verify → respond. Up to 8 tool rounds per request.
- **14 Agent Tools** — File ops, shell, git, web search, Joplin notes read/write/search.
- **6 Built-in Skills** — `/brainstorm`, `/plan`, `/tdd`, `/debug`, `/review`, `/research`. Anthropics SKILL.md format.
- **Self-Improvement Engine** — Detects skill gaps → searches GitHub for skills (5,400+ ecosystem) → installs or AI-generates new ones → logs everything.
- **Persistent Memory** — User profile, deduped facts, learnings, task history across sessions.
- **Sub-Agent Spawning** — Parallel workers for complex tasks with scoped context.

### Knowledge & Notes
- **Joplin Integration** — Full Data API client. Browse notebooks, CRUD notes, search, sync with wiki. Agent can read/write notes autonomously.
- **Voice Notes (Whisper)** — Speech-to-text via local Whisper. Record → transcribe → save to Joplin.
- **Wiki Knowledge Base** — Markdown articles with semantic search and automatic RAG indexing.
- **Tiered Context (L0/L1/L2)** — OpenViking-inspired progressive context loading to reduce token waste.

### Development IDE
- **Monaco Editor** — VS Code engine with Python syntax, Ctrl+Enter execution, Ask AI.
- **GitHub Integration** — Clone, browse, pull, index repos into knowledge base.
- **Code Execution** — Sandboxed Python runner with timeout.

### Autonomous Learning (the sun ☀️)
- **Daily Auto-Learner** — Discovers top 20 trending GitHub repos worldwide, clones/analyzes READMEs, extracts technologies, builds wiki articles and tiered context.
- **3-Pass Processing** — Each repo processed up to 3 times (overview → technical deep-dive → patterns & lessons), then skipped.
- **Fact Checker (2x frequency)** — Cross-references wiki claims against live GitHub API, detects contradictions in memory, flags stale context. Runs every 12h (twice as often as learning).
- **Self-Maintenance (every 6h)** — Syntax verification, dependency audits, endpoint health checks, memory integrity, storage cleanup, self-tests, documentation freshness. Outputs a health score 0-100%.

### Workflow Automation
- **Define workflows** — JSON step sequences chaining agent tools together.
- **4 Built-in Templates** — Git summary, code quality, research & document, wiki backup.
- **Scheduled Runner** — Background async loop for daily/hourly/custom intervals.
- **Execution History** — Per-step results, duration, success/failure tracking.

### Observability
- **HUD Status Bar** — Real-time agent activity (Thinking → Running tools → Responding).
- **Audit Logger** — Immutable append-only log of every action (14 categories).
- **Hardware Auto-Detection** — Selects best model for your RAM/GPU.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│            Browser UI (HUD + 6 Tabs)                      │
│  Chat │ IDE (Monaco) │ Wiki │ Notes │ GitHub │ Settings    │
├──────────────────────────────────────────────────────────┤
│                FastAPI Server (:8800)                      │
│  ┌────────────────────────────────────────────────────┐  │
│  │               Agent Core                            │  │
│  │  14 Tools │ Skills │ Memory │ Sub-Agents            │  │
│  │  Self-Improve │ Workflows │ Scheduler               │  │
│  │  Tiered Context (L0/L1/L2) │ Audit Log              │  │
│  └────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────┐  │
│  │           Autonomous Systems (always-on)            │  │
│  │  Auto-Learner (24h) │ Fact Checker (12h)            │  │
│  │  Self-Maintenance (6h) │ Scheduled Workflows        │  │
│  └────────────────────────────────────────────────────┘  │
│  LLM │ Wiki │ OneNote │ Whisper │ Vector │ Executor       │
├──────────────────────────────────────────────────────────┤
│  Ollama (:11434)  │  ChromaDB  │  Microsoft Graph         │
└──────────────────────────────────────────────────────────┘
```

## Prerequisites

1. **Python 3.10+**
2. **Ollama** — Download from [ollama.com](https://ollama.com)
3. **Git** — For GitHub repo cloning

## Quick Start

### 1. Install Ollama

Download and install from [ollama.com](https://ollama.com). Then start it:

```bash
ollama serve
```

### 2. Pull a model

The app auto-detects your hardware and recommends a model:

| RAM    | GPU  | Model                  | Notes                    |
|--------|------|------------------------|--------------------------|
| ≤ 8GB  | No   | `qwen2.5-coder:1.5b`  | Fast, lightweight        |
| 16GB   | No   | `qwen2.5-coder:7b`    | Best CPU balance         |
| 16GB+  | Yes  | `qwen2.5-coder:7b`    | GPU-accelerated          |

Pull your model:

```bash
ollama pull qwen2.5-coder:7b
```

Or pull from the Settings page in the UI after starting CodePilot.

### 3. Install dependencies

```bash
cd codepilot
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt
```

### 4. Configure (optional)

```bash
copy .env.example .env
# Edit .env to set GITHUB_TOKEN, override model, etc.
```

### 5. Run

```bash
python run.py
```

Open **http://localhost:8800** in your browser.

## Usage

### Chat

Type a message and press Enter. CodePilot streams responses with syntax-highlighted code blocks. Toggle the **RAG** button (brain icon) to include context from your wiki and indexed repos.

### IDE

- Write Python code in the Monaco editor
- **Ctrl+Enter** — Run code
- **Ctrl+Shift+A** — Ask AI about the code
- Output appears in the panel below the editor

### Wiki

Create markdown articles to build your local knowledge base. Articles are automatically indexed into ChromaDB for semantic search and used as context in RAG-enhanced chat.

### GitHub

1. Set your GitHub token in Settings (needed for private repos; public repos work without one)
2. Clone repositories via the Clone button
3. Browse files, open them in the IDE
4. Click **Index for RAG** to add Python files to the knowledge base

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | System health + LLM status |
| `/api/hardware` | GET | Detected hardware profile |
| `/api/models` | GET | List installed Ollama models |
| `/api/models/switch` | POST | Switch active model |
| `/api/models/pull` | POST | Pull a new model (streaming) |
| `/api/chat` | POST | Chat with streaming response (NDJSON) |
| `/ws/chat` | WS | WebSocket chat alternative |
| `/api/conversations` | GET | List conversations |
| `/api/conversations/{id}` | GET/DELETE | Get or delete a conversation |
| `/api/code/run` | POST | Execute Python code |
| `/api/wiki` | GET/POST | List or create wiki articles |
| `/api/wiki/{slug}` | GET/PUT/DELETE | CRUD on wiki articles |
| `/api/wiki/{slug}/html` | GET | Render article as HTML |
| `/api/wiki/search/{q}` | GET | Semantic wiki search |
| `/api/wiki/reindex` | POST | Re-index all wiki articles |
| `/api/github/repos/local` | GET | List cloned repos |
| `/api/github/repos/clone` | POST | Clone a repo |
| `/api/github/repos/{name}/pull` | POST | Pull latest changes |
| `/api/github/repos/{name}/browse` | GET | Browse repo files |
| `/api/github/repos/{name}/file` | GET | Read a file |
| `/api/github/repos/{name}/index` | POST | Index repo for RAG |
| `/api/search` | GET | Search wiki + code |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `CODEPILOT_MODEL` | Auto-detected | Override default model |
| `GITHUB_TOKEN` | (none) | GitHub personal access token |
| `CODE_EXEC_TIMEOUT` | `30` | Code execution timeout (seconds) |
| `CODE_EXEC_ENABLED` | `true` | Enable/disable code execution |

## Project Structure

```
codepilot/
├── backend/
│   ├── main.py              # FastAPI server + all API routes
│   ├── config.py             # Settings, hardware detection
│   ├── llm_service.py        # Ollama LLM integration
│   ├── github_service.py     # GitHub clone/browse/index
│   ├── wiki_service.py       # Markdown wiki + vector indexing
│   ├── vector_store.py       # ChromaDB semantic search
│   ├── code_executor.py      # Sandboxed Python execution
│   └── conversations.py      # Chat history persistence
├── frontend/
│   ├── index.html            # Single-page app
│   └── js/app.js             # Frontend logic
├── data/                     # Auto-created at runtime
│   ├── wiki/                 # Markdown wiki articles
│   ├── repos/                # Cloned GitHub repos
│   ├── chroma/               # Vector DB storage
│   └── conversations/        # Chat history
├── requirements.txt
├── run.py                    # Entry point
├── .env.example
└── README.md
```

## License

Private / Internal Use.
