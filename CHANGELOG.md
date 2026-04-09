# Changelog

## v3.0.0 — Self-Improving AI Agent with Workflows

CodePilot becomes a self-improving personal AI assistant that auto-researches gaps, acquires skills from GitHub, creates its own skills, automates workflows, and documents everything.

### Self-Improvement Engine (`backend/agent/self_improve.py`)
- **Gap Detection** — Assesses whether it has the skills for a task, identifies domains it's missing
- **GitHub Skill Search** — Searches GitHub for SKILL.md files matching the Anthropics standard format
- **Skill Installation** — Downloads and installs skills from any GitHub repo (anthropics/skills, vercel-labs/agent-skills, openclaw/skills, etc.)
- **AI Skill Creation** — When no GitHub skill matches, generates a new SKILL.md using the LLM
- **Auto-Improve Loop** — Full cycle: detect gap → search GitHub → install or create → log everything
- Compatible with the entire skills ecosystem: anthropics/skills, vercel-labs/skills, openclaw/skills (5,400+ skills)

### Workflow Automation (`backend/agent/workflows.py`)
- **Define workflows** as JSON step sequences that chain agent tools together
- **4 built-in templates**: Daily Git Summary, Code Quality Check, Research & Document, Backup Wiki to Joplin
- **Run manually or on schedule** (manual, daily, weekly, hourly)
- **Execution history** with per-step results, duration, success/failure tracking
- **Create custom workflows** via API or UI

### Audit Logger (`backend/agent/audit_log.py`)
- **Immutable append-only JSONL log** of all agent actions
- Tracks: skill installs/creates, self-research, workflow runs, tool calls, notes, wiki, code changes, errors
- **Daily summary** with action counts by type
- **Search** audit log by keyword
- 14 action categories tracked

### API Additions
- `GET /api/self-improve/assess?task=...` — Assess skill gaps
- `GET /api/self-improve/search?q=...` — Search GitHub for skills
- `POST /api/self-improve/install` — Install skill from GitHub repo
- `POST /api/self-improve/create?task=...` — AI-generate a new skill
- `POST /api/self-improve/auto?task=...` — Full auto-improvement cycle
- `GET /api/self-improve/installed` — List installed GitHub skills
- `GET/POST/DELETE /api/workflows` — Workflow CRUD
- `POST /api/workflows/:id/run` — Execute a workflow
- `GET /api/workflows/:id/history` — Execution history
- `GET /api/workflows/templates` — Pre-built templates
- `GET /api/audit` — Recent audit entries
- `GET /api/audit/summary` — Daily action summary
- `GET /api/audit/search?q=...` — Search audit log

---

## v2.1.0 — Joplin Note-Taking Integration

### Joplin Integration
- **Full Joplin Data API client** (`backend/joplin_service.py`) — connects to Joplin desktop (port 41184)
- **Notes tab** in the UI — browse notebooks, view/create/edit/delete notes, search, notebook filtering
- **3 Agent tools** — `joplin_search`, `joplin_read`, `joplin_write` — the AI agent can search, read, and create notes autonomously
- **Joplin ↔ Wiki sync** — import Joplin notes into the wiki (`To Wiki` button) or export wiki articles to Joplin
- **AI note-taking** — agent saves notes to a dedicated "CodePilot" notebook with `ai-generated` tag
- **Chat → Joplin** — export any conversation as a Joplin note via `/api/joplin/chat-summary/{conv_id}`
- **Ask AI about notes** — click "Ask AI" on any note to send it to chat for analysis/expansion

### Joplin API Endpoints
- `GET /api/joplin/status` — connection status
- `POST /api/joplin/token` — set API token
- `GET /api/joplin/notebooks` — list notebooks
- `GET /api/joplin/notes` — list recent notes
- `GET/POST/PUT/DELETE /api/joplin/notes/:id` — note CRUD
- `GET /api/joplin/search` — full-text search
- `POST /api/joplin/notes/:id/to-wiki` — sync note to wiki
- `POST /api/joplin/wiki/:slug/to-joplin` — sync wiki to Joplin
- `POST /api/joplin/ai-note` — create AI-generated note
- `POST /api/joplin/chat-summary/:id` — save chat as note

### Setup
1. Open Joplin Desktop → Settings → Web Clipper → Enable
2. Copy the API token
3. In CodePilot, go to the Notes tab and paste the token (or set `JOPLIN_TOKEN` env var)

---

## v2.0.0 — AI Coding Agent

CodePilot evolves from a chat wrapper into a full AI coding agent with tools, skills, and memory.

### Agent Core
- **Agentic Loop**: Plan → use tools → verify → respond. Multi-round tool calling with up to 8 rounds per request
- **11 Agent Tools**: read_file, write_file, edit_file, list_directory, search_files, run_shell, run_python, git_status, git_diff, git_commit, web_search
- **6 Built-in Skills** (markdown-based, progressively loaded):
  - `/brainstorm` — Refine ideas, explore alternatives, validate design
  - `/plan` — Break work into 2-5 min tasks with verification steps
  - `/tdd` — RED-GREEN-REFACTOR test-driven development cycle
  - `/debug` — 4-phase systematic root cause analysis
  - `/review` — Code review for correctness, security, style, performance
  - `/research` — Web research synthesised into wiki articles
- **Persistent Memory**: User profile, facts (deduped), learnings (self-improving), task history
- **HUD Status Bar**: Real-time agent status (Thinking → Running tools → Responding) in the UI header
- **Tool Activity Display**: Tool calls and results shown inline in chat messages

### API Additions
- `GET /api/skills` — List all skills
- `POST /api/skills` — Create user skills
- `DELETE /api/skills/{slug}` — Delete user skills
- `GET /api/memory` — View memory (profile, facts, learnings, tasks)
- `PUT /api/memory/profile` — Update user profile
- `POST /api/memory/facts` — Add facts to memory
- `GET /api/tools` — List available agent tools

### Architecture
- Inspired by: hermes-agent (self-improving), deer-flow (sub-agents, sandbox), superpowers (workflow methodology), OpenViking (tiered context), karpathy-skills (coding principles), everything-claude-code (4-layer architecture)
- 32 GitHub repos researched and analysed — see `docs/REPO-ANALYSIS.md`

---

## v1.0.0 — Initial Release

### Features
- **AI Chat**: Streaming chat via Ollama with configurable models
- **RAG Integration**: Retrieval-Augmented Generation using wiki articles and indexed code
- **IDE**: Monaco Editor with Python syntax, Ctrl+Enter execution, Ask AI
- **Wiki Knowledge Base**: Markdown articles with YAML frontmatter, semantic search, auto-indexing
- **GitHub Integration**: Clone, browse, pull, and index repos
- **Code Execution**: Sandboxed Python subprocess runner with timeout
- **Hardware Auto-Detection**: Auto-selects model based on RAM/GPU
- **Conversation History**: Persistent chat history with JSON storage
- **WebSocket Support**: Alternative WS endpoint for real-time chat

### Models Supported
- `qwen2.5-coder:0.5b` — Ultra-light (< 8GB RAM)
- `qwen2.5-coder:1.5b` — Light (8GB RAM)
- `qwen2.5-coder:7b` — Standard (16GB RAM or GPU)
- Any Ollama-compatible model via Settings pull
