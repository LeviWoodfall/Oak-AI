# GitHub Repo Analysis — CodePilot Integration Roadmap

Investigated 22 repos. Categorised by integration value for CodePilot.

---

## Tier 1: HIGH VALUE — Direct Integration

### 1. HKUDS/nanobot (Python, 38.6k★)
**What it is:** Ultra-lightweight personal AI agent with memory, tools, Python SDK, and OpenAI-compatible API.

**Why it matters for CodePilot:**
- **Layered memory system** — `SOUL.md`, `USER.md`, `MEMORY.md` files + `history.jsonl` for append-only summarized history. "Dream" process consolidates memory on schedule. This is exactly what CodePilot's wiki needs — automated knowledge consolidation from chat history.
- **Python SDK** — `from nanobot import Nanobot; await bot.run("...")` with session isolation. Could wrap CodePilot's LLM service with agent capabilities (tool use, multi-step reasoning).
- **Agent hooks** — `before_execute_tools`, lifecycle callbacks. Could add tool-use to CodePilot (file ops, git commands, web search).
- **OpenAI-compatible API** — Exposes at `127.0.0.1:8900`. Could sit alongside CodePilot or replace raw Ollama calls.

**Integration path:** Clone → index into knowledge base → study the memory architecture → port the Dream/memory consolidation pattern into CodePilot's wiki service. The agent hook pattern could add tool-calling to chat.

---

### 2. karpathy/autoresearch (Python, 69k★)
**What it is:** AI agents that autonomously run ML research experiments on a single GPU. Agent edits `train.py`, runs 5-minute experiments, iterates.

**Why it matters for CodePilot:**
- **The pattern is gold** — an AI agent that edits code, runs it, measures results, and iterates. This is exactly the "agentic coding" loop CodePilot should have.
- **`program.md` as a "skill"** — a lightweight instruction file that tells the agent what to do. CodePilot could use this pattern for wiki-based task definitions.
- **Single-GPU design** — aligns with CodePilot's "run anywhere" philosophy.
- **Self-contained** — 3 files: `prepare.py`, `train.py`, `program.md`. Clean architecture to learn from.

**Integration path:** Clone → index → study the agent loop pattern (edit → run → measure → iterate). Implement this as a "Code Research" mode in CodePilot where the AI can autonomously refine code against a test/metric.

---

### 3. run-llama/liteparse (TypeScript, 4k★)
**What it is:** Fast, local document parser. PDF, DOCX, PPTX, images → structured text with bounding boxes. Uses Tesseract.js for OCR. Runs entirely locally, no cloud.

**Why it matters for CodePilot:**
- **Supercharges the wiki** — feed PDFs, docs, slides into the knowledge base. Currently wiki only accepts manually typed markdown.
- **Multi-format input** — PDF, DOCX, PPTX, images, HTML, EPUB, CSV, TSV, RTF.
- **JSON + text output** with bounding boxes — structured data for RAG.
- **CLI tool** — `liteparse file.pdf` → markdown. Dead simple to call from Python subprocess.
- **No cloud dependency** — fully local, matches CodePilot's philosophy.

**Integration path:** Install as a CLI tool. Add a "Import Document" button to the Wiki tab that runs `liteparse` on uploaded files, converts to markdown, and auto-creates a wiki article. Index into ChromaDB for RAG.

---

### 4. opendataloader-project/opendataloader-pdf (Java+Python, 12.9k★)
**What it is:** #1 ranked PDF parser for AI-ready data. Hybrid mode with OCR, table extraction, formula (LaTeX), chart descriptions. Python bindings available.

**Why it matters for CodePilot:**
- **Best-in-class accuracy** — 0.907 overall score, #1 across reading order, table, and heading extraction.
- **Python API** — `opendataloader_pdf.convert(input_path=["file.pdf"], output_dir="output/", format="markdown,json")`.
- **Table extraction** — critical for ingesting technical docs with data tables.
- **Batch processing** — feed entire folders of PDFs at once.
- **Requires Java 11+** — slightly heavier dependency than liteparse.

**Integration path:** `pip install opendataloader-pdf`. Add as an alternative/upgrade to liteparse for heavy PDF processing. Best for technical documentation, research papers, specs.

---

### 5. docker/docker-agent (Go, 2.8k★)
**What it is:** AI agent builder and runtime by Docker. YAML-defined agents with multi-agent orchestration, MCP tools, and built-in RAG.

**Why it matters for CodePilot:**
- **YAML agent definitions** — declarative, versionable. Could define CodePilot "skills" as YAML files.
- **Built-in RAG** — BM25, embeddings, hybrid search, reranking. More sophisticated than CodePilot's current ChromaDB-only approach.
- **MCP (Model Context Protocol)** — standard tool interface. Could expose CodePilot's services as MCP tools.
- **Multi-agent architecture** — delegate subtasks to specialist agents (code review agent, test agent, doc agent).
- **Docker Model Runner** — run local models without Ollama.

**Integration path:** Study the YAML agent spec and RAG implementation. Port the hybrid search (BM25 + embeddings) pattern into CodePilot's vector store. Consider adopting MCP for tool integration.

---

### 6. superagent-ai/superagent (TypeScript+Python, 6.5k★)
**What it is:** AI safety SDK — prompt injection detection, PII redaction, repo security scanning.

**Why it matters for CodePilot:**
- **Guard** — detect and block prompt injections. Important when CodePilot executes code from AI responses.
- **Redact** — remove PII/secrets from text before sending to LLM.
- **Scan** — analyze repos for AI agent-targeted attacks (repo poisoning). Critical when cloning external repos.
- **Python SDK** — `from safety_agent import create_client; await client.guard(input=msg)`.

**Integration path:** `pip install safety-agent`. Wrap CodePilot's chat input through `guard()` before sending to LLM. Run `scan()` on cloned repos before indexing. Add `redact()` for sensitive code snippets.

---

## Tier 2: MEDIUM VALUE — Feature Enhancement

### 7. code-yeongyu/oh-my-openagent (TypeScript, 49.8k★)
**What it is:** Agent harness with discipline agents (Sisyphus/Hephaestus/Prometheus), LSP integration, AST-Grep, MCP.

**Relevance:** The **discipline agent pattern** is instructive — different agents for different tasks (planner, executor, craftsman). CodePilot could adopt this: a "Planner" that outlines approach, an "Executor" that writes code, a "Reviewer" that checks quality. The LSP integration (rename, goto definition, find references, diagnostics) is exactly what CodePilot's IDE needs. However, it's TypeScript-based and tightly coupled to Claude Code — hard to integrate directly. **Best used as a design reference.**

### 8. openclaw/openclaw (TypeScript, 352k★)
**What it is:** Full personal AI assistant platform. Multi-channel (WhatsApp, Telegram, Slack, etc.), multi-agent routing, voice, browser control, skills platform.

**Relevance:** Massive scope — too big to integrate directly. But the **skills platform** concept (bundled, managed, workspace skills with on-demand lifecycle) is excellent. CodePilot could adopt a "skills" system where wiki articles tagged `skill:` become executable agent instructions. The **session management** and **model failover** patterns are also worth studying.

### 9. AmElmo/proofshot (TypeScript, 779★)
**What it is:** Verification workflow for AI coding agents. Records browser sessions, captures screenshots, collects errors, bundles proof artifacts.

**Relevance:** When CodePilot generates UI code, proofshot could **verify it actually works** — record a browser session, capture screenshots, report errors. The "start → test → stop → bundle artifacts" pattern could be added as a "Verify" button in the IDE. Requires Node.js.

### 10. standardagents/arrow-js (TypeScript, 3.4k★)
**What it is:** Tiny reactive UI framework with WASM sandboxes for safe code execution.

**Relevance:** The **WASM sandbox** (`@arrow-js/sandbox`) is interesting for CodePilot's code execution. Currently we run Python in a subprocess — WASM sandboxing would be safer. However, it's JavaScript-focused and CodePilot is Python-focused. **Lower priority but worth watching** for future JS/web code execution support.

### 11. FireRedTeam/FireRed-OCR (Python, 258★)
**What it is:** Python OCR engine based on Qwen3-VL architecture.

**Relevance:** Could add OCR capability to CodePilot's wiki — scan handwritten notes, whiteboard photos, or screenshots and convert to searchable text in the knowledge base. Requires a vision model though, which is heavy. **Better to use liteparse's built-in Tesseract** for OCR unless you need the quality boost from a vision model.

### 12. homanp/infinite-monitor (TypeScript, 504★)
**What it is:** AI-powered real-time monitoring dashboard. Describe what you want to monitor → AI writes React code → renders live.

**Relevance:** Cool concept but tangential to CodePilot's core mission. Could be used to **monitor CodePilot itself** — LLM response times, token usage, knowledge base growth. Low priority.

---

## Tier 3: REFERENCE / KNOWLEDGE BASE — Index into Wiki

### 13. x1xhlol/better-clawd (TypeScript, 374★)
Claude Code alternative with OpenAI/OpenRouter support, no telemetry. **Reference value:** Study the OpenRouter integration pattern for adding multi-provider LLM support to CodePilot (use Ollama locally, fall back to OpenRouter API).

### 14. CoderLuii/HolyClaude (Dockerfile, 1.9k★)
AI coding workstation: Claude Code + web UI + 7 AI CLIs + headless browser + 50+ tools. **Reference value:** The Dockerfile and tool composition is a blueprint for packaging CodePilot as a Docker container with batteries included.

### 15. Gen-Verse/OpenClaw-RL (Python, 4.8k★)
Train any agent by talking — RL-based agent training. **Future value:** Could enable training CodePilot's agent behaviors through conversation feedback rather than manual tuning.

### 16. mutable-state-inc/autoresearch-at-home (Python, 465★)
Fork of autoresearch optimized for home hardware. **Reference value:** Same patterns as autoresearch but with practical optimizations for consumer GPUs.

### 17. alvinreal/awesome-opensource-ai (Python, 2.4k★)
Curated list of open-source AI projects. **Wiki fodder:** Clone and index into knowledge base as a reference directory.

### 18. estuary/flow (Rust, 906★)
Real-time data synchronization between systems. **Future value:** Could sync CodePilot's knowledge base across multiple machines if you run it on different hardware.

### 19. thesysdev/openui (TypeScript, 3.2k★)
Open standard for generative UI. **Future value:** Could let CodePilot generate interactive UI components in chat responses rather than just text/code.

### 20. aquasecurity/trivy (Go, 34.4k★)
Security scanner for containers, code, and dependencies. **Practical value:** Run `trivy fs .` on cloned repos to scan for vulnerabilities before indexing. Could add a "Security Scan" button to the GitHub tab.

### 21. ripienaar/free-for-dev (HTML, 120k★)
Free SaaS/PaaS/IaaS list. **Wiki fodder:** Index as a reference article for finding free development tools.

### 22. trimstray/the-book-of-secret-knowledge (214k★)
Massive collection of CLI tools, hacks, cheatsheets. **Wiki fodder:** Index sections relevant to Python development as knowledge base articles.

---

## Integration Roadmap

### Phase 1: Knowledge Base Expansion (Week 1)
1. Clone **liteparse** + **opendataloader-pdf** → add document import to wiki (PDF/DOCX → markdown → ChromaDB)
2. Clone **autoresearch** + **nanobot** → index Python files into RAG knowledge base
3. Clone **awesome-opensource-ai** + **the-book-of-secret-knowledge** → index as reference wiki articles
4. Add **trivy** scanning on repo clone (security check before indexing)

### Phase 2: Agent Capabilities (Week 2-3)
5. Study **nanobot**'s memory architecture → implement Dream-like memory consolidation (auto-summarize chat history into wiki articles)
6. Study **autoresearch**'s agent loop → implement "Code Research" mode (edit → run → measure → iterate)
7. Study **docker-agent**'s RAG → upgrade vector store with hybrid search (BM25 + embeddings + reranking)
8. Add **superagent** safety layer → guard chat input, scan cloned repos

### Phase 3: Advanced Features (Week 4+)
9. Study **oh-my-openagent**'s discipline agents → implement multi-agent routing (Planner → Coder → Reviewer)
10. Study **openclaw**'s skills platform → implement wiki-based "skills" that agents can execute
11. Add **proofshot** verification for generated UI code
12. Study **better-clawd**'s OpenRouter integration → add API fallback for when local LLM isn't sufficient

---

## Priority Clone List

These repos should be cloned and indexed into CodePilot immediately:

```bash
# High-value Python repos (index for RAG)
HKUDS/nanobot
karpathy/autoresearch
mutable-state-inc/autoresearch-at-home
FireRedTeam/FireRed-OCR

# Install as tools
pip install opendataloader-pdf
npm install -g liteparse
pip install safety-agent

# Reference repos (clone + browse)
docker/docker-agent
code-yeongyu/oh-my-openagent
openclaw/openclaw
```

---

# Batch 2: Additional Repos (10 more)

## CRITICAL — Architecture-Defining Repos

### 23. NousResearch/hermes-agent (TypeScript, fork of OpenClaw)
**What it is:** Self-improving AI agent by Nous Research. Has a built-in **learning loop** — creates skills from experience, improves them during use, persists knowledge, searches past conversations, builds a model of who you are across sessions. Runs on a $5 VPS. Multi-provider (Nous Portal, OpenRouter 200+ models, OpenAI, or custom endpoint).

**Why it's critical for CodePilot:**
- **Self-improving skills** — the agent learns from what it does and creates reusable skills automatically
- **Memory + personality** — remembers your preferences, writing style, tech stack across sessions
- **Multi-provider model switching** — `hermes model` to swap LLMs, no code changes
- **Skills system** — `/skills` to list, `/<skill-name>` to invoke. Skills are markdown files
- **CLI + Gateway** — works as both a terminal agent AND a messaging gateway (Telegram, Discord, etc.)
- **MCP integration** — standard tool protocol
- **Migrates from OpenClaw** — `hermes claw migrate`

**Verdict:** This is the strongest candidate for CodePilot's agent backbone. It's Python/Node.js, self-improving, and already has the skill/memory/tool architecture we need.

---

### 24. bytedance/deer-flow (Python, "DeerFlow 2.0")
**What it is:** Long-horizon SuperAgent harness by ByteDance. Researches, codes, creates. Uses sandboxes, memories, tools, skills, sub-agents, and a message gateway. Handles tasks that take minutes to hours.

**Why it's critical for CodePilot:**
- **Skills as markdown files** — `SKILL.md` files define workflows loaded progressively (only when needed, not all at once). Keeps context window lean.
- **Sub-agent architecture** — lead agent spawns sub-agents, each with scoped context/tools. Run in parallel, converge results.
- **Sandbox + filesystem** — each task gets its own execution environment with uploads, workspace, outputs directories
- **Context engineering** — summarizes completed sub-tasks, offloads to filesystem, compresses old context
- **Long-term memory** — persistent user profile, preferences, tech stack, recurring workflows across sessions
- **Dedup memory** — skips duplicate fact entries so preferences don't accumulate endlessly
- **Claude Code integration** — `claude-to-deerflow` skill bridges Claude Code ↔ DeerFlow

**Verdict:** The sandbox + sub-agent + progressive skill loading architecture is the gold standard for what CodePilot should become. Combined with hermes-agent's self-improvement, this is the blueprint.

---

### 25. volcengine/OpenViking (Python/TypeScript)
**What it is:** Context Database for AI Agents by ByteDance/Volcengine. Replaces flat vector stores with a **filesystem paradigm** for managing agent memory, resources, and skills.

**Why it's critical for CodePilot:**
- **`viking://` protocol** — unified URI scheme for all context (memory, resources, skills). Agents navigate via `ls`, `find` like a developer would.
- **Tiered context loading (L0/L1/L2):**
  - L0 (Abstract): ~100 tokens — one-sentence summary for quick relevance check
  - L1 (Overview): ~2k tokens — core info for planning
  - L2 (Details): full content — loaded only when needed
- **Directory recursive retrieval** — combines directory positioning with semantic search
- **Visualized retrieval trajectory** — see exactly what context the agent loaded and why
- **Automatic session management** — compresses conversations, extracts long-term memory automatically

**Verdict:** This should REPLACE CodePilot's current flat ChromaDB vector store. The L0/L1/L2 tiered loading alone would drastically reduce token waste. The filesystem paradigm aligns perfectly with how developers think.

---

### 26. obra/superpowers (Markdown skills)
**What it is:** Agentic skills framework & software development methodology. Works with Claude Code, Cursor, Codex, OpenCode, GitHub Copilot CLI, Gemini CLI.

**Why it's critical for CodePilot:**
- **The workflow is exactly what we need:**
  1. `brainstorming` → refine ideas, explore alternatives, save design doc
  2. `using-git-worktrees` → isolated workspace on new branch
  3. `writing-plans` → break into 2-5 min tasks with exact file paths + verification
  4. `subagent-driven-development` → dispatch subagent per task with 2-stage review
  5. `test-driven-development` → RED-GREEN-REFACTOR
  6. `requesting-code-review` → review against plan, block on critical issues
  7. `finishing-a-development-branch` → verify tests, merge/PR/discard
- **Skills are pure markdown** — no code, just instructions. Portable across any agent.
- **`dispatching-parallel-agents`** — concurrent subagent workflows
- **`systematic-debugging`** — 4-phase root cause process
- **`writing-skills`** — meta-skill to create new skills

**Verdict:** Clone this IMMEDIATELY and port all skills into CodePilot's wiki/skills system. This is the definitive methodology for agentic software development.

---

### 27. affaan-m/everything-claude-code (Plugin system)
**What it is:** Agent harness performance optimization system. Skills, instincts, memory, security, and research-first development. Works across Claude Code, Codex, OpenCode, Cursor.

**Why it matters:**
- **4-layer architecture:** Agents (subagents with tools) → Skills (workflow surfaces) → Hooks (tool event handlers) → Rules (always-follow guidelines)
- **Agent YAML definitions** with scoped tools and model selection
- **Skills are the primary workflow surface** — invoked directly, suggested automatically, reused by agents
- **Hooks fire on tool events** — e.g., warn about `console.log` on file edit
- **Rules organized by language** — `common/` + `python/` + `typescript/` etc.
- **AgentShield** — security auditor skill
- **Continuous Learning v2** — learning from interactions

**Verdict:** Port the Rules system (especially `rules/python/`) into CodePilot as built-in coding guidelines. The hook system is a great pattern for the IDE — trigger actions on file save, code run, etc.

---

## HIGH VALUE — Feature Enhancement

### 28. forrestchang/andrej-karpathy-skills (Markdown)
**What it is:** A single `CLAUDE.md` file derived from Karpathy's observations on LLM coding pitfalls. 4 principles: Think Before Coding, Simplicity First, Surgical Changes, Goal-Driven Execution.

**Why it matters:** These should be **baked into CodePilot's system prompt** as core coding principles. They prevent the most common LLM coding failures (overengineering, silent assumptions, touching unrelated code, no verification).

---

### 29. pbakaus/impeccable (Design skill)
**What it is:** Design language skill with 7 domain references (typography, color, spatial, motion, interaction, responsive, UX writing) and 21 commands (`/audit`, `/critique`, `/normalize`, `/polish`, etc.)

**Why it matters:** When CodePilot generates UI/frontend code, this skill ensures it's not ugly. Port the design references into the wiki as knowledge articles. The `/audit` → `/normalize` → `/polish` workflow is great for iterating on generated UI.

---

### 30. jarrodwatts/claude-hud (Status display)
**What it is:** HUD plugin showing context usage bars, active tools, running agents, and todo progress.

**Why it matters:** CodePilot needs exactly this — a status bar showing:
- Context window usage (green → yellow → red)
- Active model + provider
- Current agent activity (reading, editing, running)
- Todo/task progress

Port this as a status bar in CodePilot's UI header.

---

### 31. mvanhorn/last30days-skill (Research skill)
**What it is:** Agent skill that researches any topic across Reddit, X, YouTube, HN, Polymarket, and the web — synthesizes a grounded summary scored by social engagement.

**Why it matters:** Add as a CodePilot skill — `/research <topic>` to get the latest community knowledge on any tech topic, library, or tool. Results feed into the wiki automatically.

---

### 32. vanjs-org/van (1.0kB UI framework)
**What it is:** World's smallest reactive UI framework. 1.0kB gzipped. 5 functions. No build step. Pure JS.

**Why it matters:** Consider replacing Tailwind CDN + vanilla JS in CodePilot's frontend with VanJS for reactive components. Ultra-lightweight aligns with CodePilot's "run anywhere" philosophy. Also useful as a reference for teaching the LLM to generate lightweight UI code.

---

# EVOLVED ARCHITECTURE VISION: CodePilot v2

## From Chat+IDE to Full AI Coding Agent

Based on all 32 repos researched, the clear direction is:

```
CodePilot v1 (current):  Chat wrapper + Monaco IDE + Wiki + GitHub browser
CodePilot v2 (target):   Self-improving AI coding agent with tools, skills,
                          memory, sub-agents, and sandboxed execution
```

## Architecture Inspired By

| Component | Inspired By | What We Take |
|-----------|-------------|--------------|
| **Agent Core** | hermes-agent + nanobot | Self-improving skills, memory system, multi-provider LLM |
| **Skills System** | superpowers + deer-flow + ECC | Markdown skills, progressive loading, workflow methodology |
| **Context DB** | OpenViking | L0/L1/L2 tiered loading, filesystem paradigm, `viking://` style URIs |
| **Tools** | deer-flow + oh-my-openagent | File ops, shell exec, git, web search, LSP integration |
| **Sub-Agents** | deer-flow + ECC | Scoped sub-agents for parallel tasks (plan, code, review, test) |
| **Sandbox** | deer-flow | Isolated execution per task with workspace/uploads/outputs |
| **Coding Rules** | karpathy-skills + ECC | Baked-in principles: think first, simplicity, surgical changes |
| **Design Skills** | impeccable | UI/UX quality when generating frontend code |
| **HUD** | claude-hud | Real-time status bar (context, tools, agents, progress) |
| **Research** | last30days-skill + autoresearch | Web research + autonomous code experimentation |
| **Memory** | hermes-agent + deer-flow + OpenViking | Long-term user profile + task memory + dedup |

## Proposed v2 Module Layout

```
codepilot/
├── backend/
│   ├── main.py              # FastAPI server
│   ├── config.py             # Hardware detection + settings
│   ├── llm_service.py        # Ollama + multi-provider support
│   │
│   ├── agent/                # NEW: Agent core
│   │   ├── agent.py          # Main agent loop (plan → execute → verify)
│   │   ├── tools.py          # Tool registry (file, shell, git, search, lsp)
│   │   ├── sub_agents.py     # Sub-agent spawner with scoped context
│   │   ├── memory.py         # Long-term memory (user profile, task history)
│   │   └── skills.py         # Skill loader (markdown → executable workflows)
│   │
│   ├── context/              # NEW: Context engine (inspired by OpenViking)
│   │   ├── context_db.py     # Tiered context store (L0/L1/L2)
│   │   ├── context_fs.py     # Filesystem paradigm (codepilot:// URIs)
│   │   └── retrieval.py      # Hybrid retrieval (directory + semantic)
│   │
│   ├── skills/               # NEW: Built-in skills (markdown)
│   │   ├── brainstorming.md
│   │   ├── writing-plans.md
│   │   ├── test-driven-dev.md
│   │   ├── code-review.md
│   │   ├── debugging.md
│   │   ├── research.md
│   │   └── impeccable.md     # Design skill
│   │
│   ├── rules/                # NEW: Coding rules (from karpathy-skills + ECC)
│   │   ├── common.md         # Think first, simplicity, surgical changes
│   │   └── python.md         # Python-specific patterns
│   │
│   ├── github_service.py
│   ├── wiki_service.py
│   ├── vector_store.py       # Upgraded: hybrid search (BM25 + embeddings)
│   ├── code_executor.py      # Upgraded: sandboxed workspace per task
│   └── conversations.py
│
├── frontend/
│   ├── index.html            # Chat + IDE + Wiki + GitHub + HUD
│   └── js/app.js             # + Agent status bar, tool activity, progress
│
├── data/
│   ├── skills/               # User-created + installed skills
│   ├── memory/               # Persistent agent memory
│   ├── wiki/
│   ├── repos/
│   └── context/              # Tiered context cache
│
├── requirements.txt
└── run.py
```

## Key Design Decisions

1. **hermes-agent as inspiration, not dependency** — We build our own agent loop in Python, taking the best patterns from hermes (self-improving skills, memory) without the Node.js dependency
2. **Skills are markdown** — Following superpowers/deer-flow pattern. Portable, readable, no code required
3. **Progressive skill loading** — Only load skills relevant to the current task (deer-flow pattern)
4. **Tiered context (L0/L1/L2)** — OpenViking's approach replaces flat vector chunks
5. **Sub-agents with scoped context** — Each sub-task gets its own context window, isolated from others
6. **Karpathy principles as system rules** — Always-on coding guidelines, not optional
7. **HUD in header** — Context usage bar, active tools, agent status, task progress
