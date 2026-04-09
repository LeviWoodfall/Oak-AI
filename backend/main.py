"""
CodePilot — FastAPI server.
Serves the chat/IDE/wiki UI and provides API endpoints for all services.
"""
import json
import logging
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

from backend.config import settings, HARDWARE, BASE_DIR
from backend.llm_service import llm_service
from backend.github_service import github_service
from backend.wiki_service import wiki_service
from backend.vector_store import vector_store
from backend.code_executor import code_executor
from backend.conversations import conversation_manager
from backend.agent.agent import coding_agent
from backend.agent.memory import agent_memory
from backend.agent.skills import skill_loader
from backend.agent.audit_log import audit_log
from backend.agent.self_improve import self_improve_engine
from backend.agent.workflows import workflow_engine
from backend.agent.sub_agents import sub_agent_spawner
from backend.agent.scheduler import workflow_scheduler
from backend.agent.tiered_context import tiered_context
from backend.whisper_service import whisper_service
from backend.joplin_service import joplin_service

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("oak")

# ── App ──────────────────────────────────────────────────────────────
app = FastAPI(title="Oak", version=settings.app_version)

FRONTEND_DIR = BASE_DIR / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ── Request models ───────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    use_rag: bool = True
    temperature: float = 0.7
    agent_mode: bool = True  # v2: use agentic loop by default

class CodeRunRequest(BaseModel):
    code: str
    timeout: Optional[int] = None

class WikiArticle(BaseModel):
    title: str
    content: str
    tags: list[str] = []

class WikiUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None

class RepoClone(BaseModel):
    url: str
    name: Optional[str] = None

class GitHubToken(BaseModel):
    token: str

class ModelSwitch(BaseModel):
    model: str


# ── Pages ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")


# ── System ───────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    llm_health = await llm_service.health_check()
    return {
        "status": "ok",
        "version": settings.app_version,
        "llm": llm_health,
        "knowledge_base": vector_store.stats(),
    }

@app.get("/api/hardware")
async def hardware():
    return HARDWARE.to_dict()


# ── LLM / Models ────────────────────────────────────────────────────

@app.get("/api/models")
async def list_models():
    models = await llm_service.list_models()
    return {"models": models, "active": llm_service.model}

@app.post("/api/models/switch")
async def switch_model(req: ModelSwitch):
    result = await llm_service.switch_model(req.model)
    return result

@app.post("/api/models/pull")
async def pull_model(req: ModelSwitch):
    async def stream():
        async for chunk in llm_service.pull_model(req.model):
            yield chunk + "\n"
    return StreamingResponse(stream(), media_type="text/plain")


# ── Chat (HTTP streaming) ───────────────────────────────────────────

@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Chat endpoint — uses agentic loop (v2) or plain LLM (v1)."""
    # Get or create conversation
    conv_id = req.conversation_id
    if not conv_id:
        conv = conversation_manager.create()
        conv_id = conv["id"]

    # Save user message
    conversation_manager.add_message(conv_id, "user", req.message)

    # Get conversation history
    conv = conversation_manager.get(conv_id)
    messages = [{"role": m["role"], "content": m["content"]} for m in conv["messages"]]

    if req.agent_mode:
        # v2: Agentic loop with tools, skills, memory
        async def agent_stream():
            full_response = ""
            yield json.dumps({"conversation_id": conv_id, "type": "start"}) + "\n"

            async for event in coding_agent.chat(
                messages=messages,
                conversation_id=conv_id,
                use_rag=req.use_rag,
                temperature=req.temperature,
            ):
                if event["type"] == "token":
                    full_response += event["content"]
                yield json.dumps(event) + "\n"

            if full_response:
                conversation_manager.add_message(conv_id, "assistant", full_response)

        return StreamingResponse(agent_stream(), media_type="application/x-ndjson")
    else:
        # v1: Plain LLM chat (no tools)
        context_docs = []
        if req.use_rag:
            context_docs = vector_store.search_all(req.message, n_results=3)

        async def plain_stream():
            full_response = ""
            yield json.dumps({"conversation_id": conv_id, "type": "start"}) + "\n"

            async for chunk in llm_service.chat(
                messages=messages,
                temperature=req.temperature,
                context_docs=context_docs,
            ):
                try:
                    data = json.loads(chunk)
                    if "message" in data and "content" in data["message"]:
                        token = data["message"]["content"]
                        full_response += token
                        yield json.dumps({"type": "token", "content": token}) + "\n"
                    if data.get("done"):
                        yield json.dumps({"type": "done"}) + "\n"
                except json.JSONDecodeError:
                    pass

            if full_response:
                conversation_manager.add_message(conv_id, "assistant", full_response)

        return StreamingResponse(plain_stream(), media_type="application/x-ndjson")


# ── Chat via WebSocket (alternative) ────────────────────────────────

@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            message = data.get("message", "")
            conv_id = data.get("conversation_id")
            use_rag = data.get("use_rag", True)

            if not conv_id:
                conv = conversation_manager.create()
                conv_id = conv["id"]

            conversation_manager.add_message(conv_id, "user", message)
            conv = conversation_manager.get(conv_id)
            messages = [{"role": m["role"], "content": m["content"]} for m in conv["messages"]]

            context_docs = []
            if use_rag:
                context_docs = vector_store.search_all(message, n_results=3)

            await ws.send_json({"type": "start", "conversation_id": conv_id})

            full_response = ""
            async for chunk in llm_service.chat(messages=messages, context_docs=context_docs):
                try:
                    parsed = json.loads(chunk)
                    if "message" in parsed and "content" in parsed["message"]:
                        token = parsed["message"]["content"]
                        full_response += token
                        await ws.send_json({"type": "token", "content": token})
                    if parsed.get("done"):
                        await ws.send_json({"type": "done"})
                except json.JSONDecodeError:
                    pass

            if full_response:
                conversation_manager.add_message(conv_id, "assistant", full_response)

    except WebSocketDisconnect:
        pass


# ── Conversations ────────────────────────────────────────────────────

@app.get("/api/conversations")
async def list_conversations():
    return {"conversations": conversation_manager.list_all()}

@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    conv = conversation_manager.get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv

@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    if conversation_manager.delete(conv_id):
        return {"status": "deleted"}
    raise HTTPException(404, "Conversation not found")


# ── Code execution ───────────────────────────────────────────────────

@app.post("/api/code/run")
async def run_code(req: CodeRunRequest):
    result = await code_executor.execute(req.code, timeout=req.timeout)
    return result


# ── Wiki ─────────────────────────────────────────────────────────────

@app.get("/api/wiki")
async def wiki_list(tag: Optional[str] = None):
    return {"articles": wiki_service.list_articles(tag=tag)}

@app.get("/api/wiki/tags")
async def wiki_tags():
    return {"tags": wiki_service.get_all_tags()}

@app.post("/api/wiki")
async def wiki_create(article: WikiArticle):
    result = wiki_service.create_article(article.title, article.content, article.tags)
    return result

@app.get("/api/wiki/{slug}")
async def wiki_get(slug: str):
    article = wiki_service.get_article(slug)
    if not article:
        raise HTTPException(404, "Article not found")
    return article

@app.put("/api/wiki/{slug}")
async def wiki_update(slug: str, update: WikiUpdate):
    result = wiki_service.update_article(slug, update.title, update.content, update.tags)
    if not result:
        raise HTTPException(404, "Article not found")
    return result

@app.delete("/api/wiki/{slug}")
async def wiki_delete(slug: str):
    if wiki_service.delete_article(slug):
        return {"status": "deleted"}
    raise HTTPException(404, "Article not found")

@app.get("/api/wiki/{slug}/html")
async def wiki_render(slug: str):
    html = wiki_service.render_html(slug)
    if not html:
        raise HTTPException(404, "Article not found")
    return {"html": html}

@app.post("/api/wiki/reindex")
async def wiki_reindex():
    count = wiki_service.reindex_all()
    return {"indexed": count}

@app.get("/api/wiki/search/{query}")
async def wiki_search(query: str, n: int = Query(default=10)):
    return {"results": wiki_service.search(query, n_results=n)}


# ── GitHub ───────────────────────────────────────────────────────────

@app.get("/api/github/status")
async def github_status():
    return {"authenticated": github_service.authenticated}

@app.post("/api/github/token")
async def github_set_token(req: GitHubToken):
    github_service.set_token(req.token)
    return {"authenticated": True}

@app.get("/api/github/repos/remote")
async def github_remote_repos(q: Optional[str] = None, limit: int = 20):
    return {"repos": github_service.list_remote_repos(query=q, limit=limit)}

@app.get("/api/github/repos/local")
async def github_local_repos():
    return {"repos": github_service.list_local_repos()}

@app.post("/api/github/repos/clone")
async def github_clone(req: RepoClone):
    result = github_service.clone_repo(req.url, req.name)
    return result

@app.post("/api/github/repos/{name}/pull")
async def github_pull(name: str):
    return github_service.pull_repo(name)

@app.delete("/api/github/repos/{name}")
async def github_delete_repo(name: str):
    vector_store.remove_repo(name)
    return github_service.delete_repo(name)

@app.get("/api/github/repos/{name}/browse")
async def github_browse(name: str, path: str = ""):
    return {"files": github_service.browse_repo(name, path)}

@app.get("/api/github/repos/{name}/file")
async def github_read_file(name: str, path: str = Query(...)):
    content = github_service.read_file(name, path)
    if content is None:
        raise HTTPException(404, "File not found")
    return {"content": content, "path": path}

@app.post("/api/github/repos/{name}/index")
async def github_index_repo(name: str):
    """Index all Python files from a repo into the knowledge base."""
    py_files = github_service.get_python_files(name)
    files = {}
    for fp in py_files:
        content = github_service.read_file(name, fp)
        if content:
            files[fp] = content
    count = vector_store.index_repo(name, files)
    return {"indexed_files": count, "total_python_files": len(py_files)}


# ── Knowledge search ─────────────────────────────────────────────────

@app.get("/api/search")
async def search_knowledge(q: str = Query(...), n: int = 5):
    """Search across wiki and code."""
    wiki_results = vector_store.search_wiki(q, n_results=n)
    code_results = vector_store.search_code(q, n_results=n)
    return {"wiki": wiki_results, "code": code_results}


# ── Agent: Skills ────────────────────────────────────────────────────

@app.get("/api/skills")
async def list_skills():
    return {"skills": skill_loader.list_skills()}

@app.get("/api/skills/{slug}")
async def get_skill(slug: str):
    skill = skill_loader.get(slug)
    if not skill:
        raise HTTPException(404, "Skill not found")
    return skill.to_dict() | {"content": skill.content}

class SkillCreate(BaseModel):
    slug: str
    title: str
    description: str
    content: str
    tools: list[str] = []
    tags: list[str] = []

@app.post("/api/skills")
async def create_skill(req: SkillCreate):
    skill = skill_loader.create_skill(
        req.slug, req.title, req.description, req.content, req.tools, req.tags
    )
    return skill.to_dict()

@app.delete("/api/skills/{slug}")
async def delete_skill(slug: str):
    if skill_loader.delete_skill(slug):
        return {"status": "deleted"}
    raise HTTPException(404, "Skill not found or is a builtin")

@app.post("/api/skills/reload")
async def reload_skills():
    skill_loader.reload()
    return {"skills": len(skill_loader.list_skills())}


# ── Agent: Memory ────────────────────────────────────────────────────

@app.get("/api/memory")
async def get_memory():
    return {
        "profile": agent_memory.get_profile(),
        "stats": agent_memory.stats(),
        "recent_facts": agent_memory.get_facts(limit=20),
        "recent_learnings": agent_memory.get_learnings(limit=10),
        "recent_tasks": agent_memory.get_recent_tasks(limit=10),
    }

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    coding_style: Optional[str] = None
    tech_stack: Optional[list[str]] = None

@app.put("/api/memory/profile")
async def update_profile(req: ProfileUpdate):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    return agent_memory.update_profile(**updates)

class FactAdd(BaseModel):
    fact: str
    source: str = "manual"

@app.post("/api/memory/facts")
async def add_fact(req: FactAdd):
    added = agent_memory.add_fact(req.fact, req.source)
    return {"added": added}

@app.get("/api/memory/search")
async def search_memory(q: str = Query(...)):
    return {"facts": agent_memory.search_facts(q)}


# ── Agent: Tools ─────────────────────────────────────────────────────

@app.get("/api/tools")
async def list_tools():
    return {"tools": coding_agent.tools.available_tools}


# ── Self-Improvement ─────────────────────────────────────────────────

@app.get("/api/self-improve/assess")
async def assess_capability(task: str = Query(...)):
    available = skill_loader.list_skills()
    return await self_improve_engine.assess_capability(task, available)

@app.get("/api/self-improve/search")
async def search_skills_github(q: str = Query(...), limit: int = 10):
    return {"results": await self_improve_engine.search_github_skills(q, limit)}

class SkillInstallRequest(BaseModel):
    repo: str
    path: str = ""

@app.post("/api/self-improve/install")
async def install_skill_from_github(req: SkillInstallRequest):
    result = await self_improve_engine.install_skill_from_github(req.repo, req.path)
    if not result:
        raise HTTPException(400, "Failed to install skill")
    return result

@app.post("/api/self-improve/create")
async def create_skill_for_task(task: str = Query(...), context: str = ""):
    return await self_improve_engine.create_skill_for_task(task, context)

@app.post("/api/self-improve/auto")
async def auto_improve(task: str = Query(...)):
    return await self_improve_engine.auto_improve(task)

@app.get("/api/self-improve/installed")
async def list_installed_skills():
    return {"installed": self_improve_engine.list_installed()}


# ── Workflows ────────────────────────────────────────────────────────

@app.get("/api/workflows")
async def list_workflows():
    return {"workflows": workflow_engine.list_all()}

@app.get("/api/workflows/templates")
async def workflow_templates():
    return {"templates": workflow_engine.get_templates()}

class WorkflowCreate(BaseModel):
    name: str
    description: str
    steps: list[dict]
    schedule: str = "manual"
    tags: list[str] = []

@app.post("/api/workflows")
async def create_workflow(req: WorkflowCreate):
    wf = workflow_engine.create(req.name, req.description, req.steps, req.schedule, req.tags)
    return wf.to_dict()

@app.get("/api/workflows/{wf_id}")
async def get_workflow(wf_id: str):
    wf = workflow_engine.get(wf_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf.to_dict()

@app.post("/api/workflows/{wf_id}/run")
async def run_workflow(wf_id: str):
    result = await workflow_engine.run(wf_id)
    return result

@app.delete("/api/workflows/{wf_id}")
async def delete_workflow(wf_id: str):
    if workflow_engine.delete(wf_id):
        return {"status": "deleted"}
    raise HTTPException(404, "Workflow not found")

@app.get("/api/workflows/{wf_id}/history")
async def workflow_history(wf_id: str, limit: int = 20):
    return {"history": workflow_engine.get_execution_history(wf_id, limit)}


# ── Audit Log ────────────────────────────────────────────────────────

@app.get("/api/audit")
async def get_audit_log(limit: int = 50, action: Optional[str] = None):
    return {"entries": audit_log.get_recent(limit, action)}

@app.get("/api/audit/summary")
async def audit_summary(date: Optional[str] = None):
    return audit_log.get_daily_summary(date)

@app.get("/api/audit/search")
async def search_audit(q: str = Query(...)):
    return {"results": audit_log.search(q)}


# ── Whisper Speech-to-Text ────────────────────────────────────────────

@app.get("/api/whisper/status")
async def whisper_status():
    return whisper_service.status()

@app.post("/api/whisper/load")
async def whisper_load_model(model: str = "base"):
    whisper_service.load_model(model)
    return whisper_service.status()

class WhisperRequest(BaseModel):
    audio_base64: str
    filename: str = "recording.webm"
    language: Optional[str] = None
    title: str = ""
    save_to_joplin: bool = True
    tags: list[str] = []

@app.post("/api/whisper/transcribe")
async def whisper_transcribe(req: WhisperRequest):
    if req.save_to_joplin:
        return await whisper_service.transcribe_and_save_note(
            req.audio_base64, req.title, req.language, req.tags
        )
    return await whisper_service.transcribe_base64(
        req.audio_base64, req.filename, req.language
    )


# ── Sub-Agents ───────────────────────────────────────────────────────

class SubAgentRequest(BaseModel):
    tasks: list[dict]
    max_parallel: int = 3

@app.post("/api/sub-agents/spawn")
async def spawn_sub_agents(req: SubAgentRequest):
    results = await sub_agent_spawner.spawn(req.tasks, req.max_parallel)
    return {"results": results}

@app.get("/api/sub-agents/active")
async def active_sub_agents():
    return {"active": sub_agent_spawner.get_active()}


# ── Scheduler ────────────────────────────────────────────────────────

@app.get("/api/scheduler/status")
async def scheduler_status():
    return workflow_scheduler.status()

@app.post("/api/scheduler/start")
async def scheduler_start():
    workflow_scheduler.start()
    return {"status": "started"}

@app.post("/api/scheduler/stop")
async def scheduler_stop():
    workflow_scheduler.stop()
    return {"status": "stopped"}


# ── Tiered Context ───────────────────────────────────────────────────

@app.get("/api/context/stats")
async def context_stats():
    return tiered_context.stats()

@app.get("/api/context/search")
async def context_search(q: str = Query(...), tier: int = 0, n: int = 10):
    return {"results": tiered_context.search(q, max_results=n, tier=tier)}

@app.get("/api/context/entries")
async def context_entries(source: Optional[str] = None):
    return {"entries": tiered_context.list_all(source_filter=source)}

class ContextIngest(BaseModel):
    uri: str
    title: str
    content: str
    source: str = ""
    tags: list[str] = []

@app.post("/api/context/ingest")
async def context_ingest(req: ContextIngest):
    entry = tiered_context.ingest(req.uri, req.title, req.content, req.source, req.tags)
    return entry.to_dict()

@app.delete("/api/context/{uri:path}")
async def context_remove(uri: str):
    if tiered_context.remove(uri):
        return {"status": "removed"}
    raise HTTPException(404, "Context entry not found")


# ── Joplin Notes ─────────────────────────────────────────────────────

@app.get("/api/joplin/status")
async def joplin_status():
    status = await joplin_service.ping()
    return status

class JoplinToken(BaseModel):
    token: str

@app.post("/api/joplin/token")
async def joplin_set_token(req: JoplinToken):
    joplin_service.set_token(req.token)
    status = await joplin_service.ping()
    return status

@app.get("/api/joplin/notebooks")
async def joplin_notebooks():
    notebooks = await joplin_service.list_notebooks()
    return {"notebooks": notebooks}

@app.get("/api/joplin/notebooks/{notebook_id}/notes")
async def joplin_notebook_notes(notebook_id: str):
    notes = await joplin_service.get_notebook_notes(notebook_id)
    return {"notes": notes}

@app.get("/api/joplin/notes")
async def joplin_list_notes(limit: int = 50):
    notes = await joplin_service.list_notes(limit=limit)
    return {"notes": notes}

@app.get("/api/joplin/notes/{note_id}")
async def joplin_get_note(note_id: str):
    note = await joplin_service.get_note(note_id)
    if not note:
        raise HTTPException(404, "Note not found")
    tags = await joplin_service.get_note_tags(note_id)
    note["tags"] = [t["title"] for t in tags]
    return note

class JoplinNote(BaseModel):
    title: str
    body: str
    notebook_id: str = ""
    tags: list[str] = []
    is_todo: bool = False

@app.post("/api/joplin/notes")
async def joplin_create_note(req: JoplinNote):
    note = await joplin_service.create_note(
        req.title, req.body, req.notebook_id, req.tags, req.is_todo
    )
    return note

class JoplinNoteUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None

@app.put("/api/joplin/notes/{note_id}")
async def joplin_update_note(note_id: str, req: JoplinNoteUpdate):
    note = await joplin_service.update_note(note_id, req.title, req.body)
    return note

@app.delete("/api/joplin/notes/{note_id}")
async def joplin_delete_note(note_id: str):
    if await joplin_service.delete_note(note_id):
        return {"status": "deleted"}
    raise HTTPException(404, "Note not found")

@app.get("/api/joplin/search")
async def joplin_search(q: str = Query(...), limit: int = 20):
    notes = await joplin_service.search_notes(q, limit=limit)
    return {"results": notes}

@app.get("/api/joplin/tags")
async def joplin_tags():
    tags = await joplin_service.list_tags()
    return {"tags": tags}

# Joplin ↔ Wiki sync
@app.post("/api/joplin/notes/{note_id}/to-wiki")
async def joplin_note_to_wiki(note_id: str):
    article = await joplin_service.sync_note_to_wiki(note_id)
    if not article:
        raise HTTPException(404, "Note not found")
    return {"status": "synced", "wiki_slug": article["slug"]}

@app.post("/api/joplin/wiki/{wiki_slug}/to-joplin")
async def wiki_to_joplin(wiki_slug: str, notebook_id: str = ""):
    note = await joplin_service.sync_wiki_to_note(wiki_slug, notebook_id)
    if not note:
        raise HTTPException(404, "Wiki article not found")
    return {"status": "synced", "note_id": note.get("id", "")}

# AI note-taking
class AINoteRequest(BaseModel):
    title: str
    content: str
    tags: list[str] = []

@app.post("/api/joplin/ai-note")
async def joplin_ai_note(req: AINoteRequest):
    note = await joplin_service.save_ai_note(req.title, req.content, req.tags)
    return note

@app.post("/api/joplin/chat-summary/{conv_id}")
async def joplin_save_chat_summary(conv_id: str):
    conv = conversation_manager.get(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    msgs = conv.get("messages", [])
    summary_parts = []
    for m in msgs:
        role = "**You**" if m["role"] == "user" else "**CodePilot**"
        summary_parts.append(f"{role}: {m['content'][:500]}")
    summary = "\n\n---\n\n".join(summary_parts)
    note = await joplin_service.save_chat_summary(conv["title"], summary)
    return {"status": "saved", "note_id": note.get("id", "")}


# ── Run ──────────────────────────────────────────────────────────────

def start():
    import uvicorn
    banner = f"""
    ╔═══════════════════════════════════════════╗
    ║              Oak v{settings.app_version}                  ║
    ║     Self-Improving AI Coding Agent        ║
    ╠═══════════════════════════════════════════╣
    ║  RAM: {HARDWARE.ram_gb}GB  |  CPUs: {HARDWARE.cpu_count}  |  GPU: {'Yes' if HARDWARE.gpu['available'] else 'No'}   ║
    ║  Model: {llm_service.model:<33}║
    ║  URL: http://{settings.host}:{settings.port:<24}║
    ╚═══════════════════════════════════════════╝
    """
    print(banner)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    start()
