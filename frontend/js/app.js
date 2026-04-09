/**
 * Oak — Frontend Application
 * Chat + IDE (Monaco) + Wiki + GitHub integration
 */

// ═══════════════════════════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════════════════════════
const state = {
    activeTab: 'chat',
    conversationId: null,
    conversations: [],
    useRAG: true,
    agentMode: true,  // v2: agentic loop with tools by default
    streaming: false,
    monacoEditor: null,
    currentRepo: null,
    currentRepoPath: '',
    currentWikiSlug: null,
    editingWikiSlug: null,
};

// ═══════════════════════════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', async () => {
    lucide.createIcons();
    initMonaco();
    initMarkdown();
    await Promise.all([
        loadHealth(),
        loadConversations(),
        loadModels(),
    ]);
});

function initMarkdown() {
    marked.setOptions({
        highlight: (code, lang) => {
            if (lang && hljs.getLanguage(lang)) {
                return hljs.highlight(code, { language: lang }).value;
            }
            return hljs.highlightAuto(code).value;
        },
        breaks: true,
        gfm: true,
    });
}

function initMonaco() {
    require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.47.0/min/vs' } });
    require(['vs/editor/editor.main'], () => {
        monaco.editor.defineTheme('codepilot-dark', {
            base: 'vs-dark',
            inherit: true,
            rules: [],
            colors: {
                'editor.background': '#0f1117',
                'editor.foreground': '#e1e4ed',
                'editorLineNumber.foreground': '#4a4f65',
                'editorCursor.foreground': '#6c63ff',
                'editor.selectionBackground': '#6c63ff33',
                'editor.lineHighlightBackground': '#1a1d2780',
            }
        });
        state.monacoEditor = monaco.editor.create(document.getElementById('monaco-container'), {
            value: '# Welcome to Oak IDE\n# Write Python code here and click Run\n\nprint("Hello from Oak!")\n',
            language: 'python',
            theme: 'codepilot-dark',
            fontSize: 14,
            fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
            minimap: { enabled: true, scale: 2 },
            scrollBeyondLastLine: false,
            automaticLayout: true,
            tabSize: 4,
            wordWrap: 'on',
            lineNumbers: 'on',
            renderWhitespace: 'selection',
            bracketPairColorization: { enabled: true },
            padding: { top: 12 },
        });

        // Ctrl+Enter to run
        state.monacoEditor.addAction({
            id: 'run-code',
            label: 'Run Code',
            keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter],
            run: () => runEditorCode(),
        });

        // Ctrl+Shift+A to ask AI
        state.monacoEditor.addAction({
            id: 'ask-ai',
            label: 'Ask AI About Code',
            keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyA],
            run: () => askAIAboutCode(),
        });
    });
}

// ═══════════════════════════════════════════════════════════════════
// Tab Navigation
// ═══════════════════════════════════════════════════════════════════
function switchTab(tab) {
    state.activeTab = tab;
    const tabs = ['chat', 'ide', 'wiki', 'notes', 'github', 'settings'];
    tabs.forEach(t => {
        const btn = document.getElementById(`tab-${t}`);
        const panel = document.getElementById(`panel-${t}`);
        const sidebar = document.getElementById(`sidebar-${t}`);
        if (btn) {
            btn.classList.toggle('tab-active', t === tab);
            btn.classList.toggle('tab-inactive', t !== tab);
            btn.classList.toggle('text-cp-muted', t !== tab);
        }
        if (panel) panel.classList.toggle('hidden', t !== tab);
        if (sidebar) sidebar.classList.toggle('hidden', t !== tab);
    });

    // Sidebar visibility
    const sidebarEl = document.getElementById('sidebar');
    sidebarEl.classList.toggle('hidden', tab === 'settings');

    // Refresh data when switching tabs
    if (tab === 'wiki') loadWikiArticles();
    if (tab === 'notes') loadJoplinNotes();
    if (tab === 'github') loadLocalRepos();
    if (tab === 'settings') loadSettings();
    if (tab === 'ide' && state.monacoEditor) state.monacoEditor.layout();

    lucide.createIcons();
}

// ═══════════════════════════════════════════════════════════════════
// Health & Models
// ═══════════════════════════════════════════════════════════════════
async function loadHealth() {
    try {
        const resp = await fetch('/api/health');
        const data = await resp.json();
        const badge = document.getElementById('status-badge');
        if (data.llm?.ollama_running && data.llm?.model_ready) {
            badge.textContent = 'Connected';
            badge.className = 'text-xs px-2 py-0.5 rounded-full bg-cp-success/20 text-cp-success';
        } else if (data.llm?.ollama_running) {
            badge.textContent = 'Model not loaded';
            badge.className = 'text-xs px-2 py-0.5 rounded-full bg-cp-warning/20 text-cp-warning';
        } else {
            badge.textContent = 'Ollama offline';
            badge.className = 'text-xs px-2 py-0.5 rounded-full bg-cp-error/20 text-cp-error';
        }
        document.getElementById('model-label').textContent = `Model: ${data.llm?.active_model || 'none'}`;
    } catch {
        document.getElementById('status-badge').textContent = 'Offline';
    }
}

async function loadModels() {
    try {
        const resp = await fetch('/api/models');
        const data = await resp.json();
        const select = document.getElementById('model-select');
        select.innerHTML = '';
        if (data.models?.length) {
            data.models.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.name;
                opt.textContent = `${m.name} (${formatSize(m.size)})`;
                if (m.name === data.active) opt.selected = true;
                select.appendChild(opt);
            });
        } else {
            select.innerHTML = '<option>No models installed</option>';
        }
    } catch {}
}

async function switchModel() {
    const model = document.getElementById('model-select').value;
    await fetch('/api/models/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
    });
    toast(`Switched to ${model}`, 'success');
    loadHealth();
}

async function pullNewModel() {
    const name = document.getElementById('pull-model-name').value.trim();
    if (!name) return;
    const progress = document.getElementById('pull-progress');
    progress.classList.remove('hidden');
    progress.textContent = 'Pulling...';

    try {
        const resp = await fetch('/api/models/pull', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: name }),
        });
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const text = decoder.decode(value);
            try {
                const data = JSON.parse(text.trim().split('\n').pop());
                progress.textContent = data.status || text.slice(0, 100);
            } catch {
                progress.textContent = text.slice(0, 100);
            }
        }
        progress.textContent = 'Done!';
        toast(`Pulled ${name}`, 'success');
        loadModels();
        loadHealth();
    } catch (e) {
        progress.textContent = `Error: ${e.message}`;
    }
}

// ═══════════════════════════════════════════════════════════════════
// Chat
// ═══════════════════════════════════════════════════════════════════
async function loadConversations() {
    try {
        const resp = await fetch('/api/conversations');
        const data = await resp.json();
        state.conversations = data.conversations || [];
        renderConversationList();
    } catch {}
}

function renderConversationList() {
    const el = document.getElementById('conversation-list');
    if (!state.conversations.length) {
        el.innerHTML = '<p class="text-xs text-cp-muted p-2">No conversations yet</p>';
        return;
    }
    el.innerHTML = state.conversations.map(c => `
        <div class="sidebar-item flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer text-sm ${c.id === state.conversationId ? 'active' : ''}"
             onclick="loadConversation('${c.id}')">
            <i data-lucide="message-square" class="w-3.5 h-3.5 text-cp-muted flex-shrink-0"></i>
            <span class="truncate flex-1">${escapeHtml(c.title)}</span>
            <button onclick="event.stopPropagation(); deleteConversation('${c.id}')" class="opacity-0 group-hover:opacity-100 hover:text-cp-error">
                <i data-lucide="x" class="w-3 h-3"></i>
            </button>
        </div>
    `).join('');
    lucide.createIcons();
}

async function newConversation() {
    state.conversationId = null;
    document.getElementById('chat-messages').innerHTML = '';
    renderConversationList();
}

async function loadConversation(id) {
    try {
        const resp = await fetch(`/api/conversations/${id}`);
        const conv = await resp.json();
        state.conversationId = id;
        const el = document.getElementById('chat-messages');
        el.innerHTML = '';
        conv.messages.forEach(m => appendMessage(m.role, m.content));
        renderConversationList();
        el.scrollTop = el.scrollHeight;
    } catch {}
}

async function deleteConversation(id) {
    await fetch(`/api/conversations/${id}`, { method: 'DELETE' });
    if (state.conversationId === id) {
        state.conversationId = null;
        document.getElementById('chat-messages').innerHTML = '';
    }
    loadConversations();
}

function handleChatKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChat();
    }
}

function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 150) + 'px';
}

function toggleRAG() {
    state.useRAG = !state.useRAG;
    const btn = document.getElementById('rag-toggle');
    const label = document.getElementById('rag-label');
    if (state.useRAG) {
        btn.className = 'p-2.5 rounded-xl bg-cp-accent/20 text-cp-accent hover:bg-cp-accent/30 transition-colors';
        label.textContent = 'RAG: On';
        label.className = 'text-cp-accent text-xs';
    } else {
        btn.className = 'p-2.5 rounded-xl bg-cp-surface2 text-cp-muted hover:bg-cp-border transition-colors';
        label.textContent = 'RAG: Off';
        label.className = 'text-cp-muted text-xs';
    }
}

async function sendChat() {
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    if (!message || state.streaming) return;

    input.value = '';
    input.style.height = 'auto';
    state.streaming = true;
    updateHUD('thinking', '');

    appendMessage('user', message);
    const assistantEl = appendMessage('assistant', '');
    const contentEl = assistantEl.querySelector('.msg-content');

    // Show typing indicator
    contentEl.innerHTML = '<span class="typing-dot inline-block w-2 h-2 bg-cp-accent rounded-full mx-0.5"></span><span class="typing-dot inline-block w-2 h-2 bg-cp-accent rounded-full mx-0.5"></span><span class="typing-dot inline-block w-2 h-2 bg-cp-accent rounded-full mx-0.5"></span>';

    try {
        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message,
                conversation_id: state.conversationId,
                use_rag: state.useRAG,
                agent_mode: state.agentMode,
            }),
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let fullText = '';
        let started = false;
        let toolCalls = [];

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const lines = decoder.decode(value).split('\n');
            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const data = JSON.parse(line);
                    if (data.conversation_id && !state.conversationId) {
                        state.conversationId = data.conversation_id;
                    }

                    // Agent status updates
                    if (data.type === 'status') {
                        updateHUD(data.status, '');
                    }

                    // Tool call events
                    if (data.type === 'tool_call') {
                        toolCalls.push(data.tool);
                        updateHUD('tool', data.tool);
                        appendToolEvent(assistantEl, 'call', data.tool, JSON.stringify(data.params || {}).slice(0, 80));
                    }

                    // Tool result events
                    if (data.type === 'tool_result') {
                        appendToolEvent(assistantEl, 'result', data.tool, (data.result || '').slice(0, 120));
                    }

                    // Token streaming
                    if (data.type === 'token') {
                        if (!started) {
                            contentEl.innerHTML = '';
                            started = true;
                            updateHUD('responding', '');
                        }
                        fullText += data.content;
                        contentEl.innerHTML = marked.parse(fullText);
                    }

                    // Done
                    if (data.type === 'done') {
                        contentEl.innerHTML = marked.parse(fullText);
                        contentEl.querySelectorAll('pre code').forEach(block => {
                            hljs.highlightElement(block);
                        });
                        addCopyButtons(contentEl);
                        if (data.tool_calls?.length) {
                            appendToolSummary(assistantEl, data.tool_calls);
                        }
                        updateHUD('idle', '');
                    }

                    if (data.error) {
                        contentEl.innerHTML = `<span class="text-cp-error">${escapeHtml(data.error)}</span>`;
                        updateHUD('error', '');
                    }
                } catch {}
            }
        }
        loadConversations();
    } catch (e) {
        contentEl.innerHTML = `<span class="text-cp-error">Error: ${escapeHtml(e.message)}</span>`;
        updateHUD('error', '');
    }

    state.streaming = false;
    scrollChat();
}

function appendToolEvent(messageEl, kind, tool, detail) {
    const container = messageEl.querySelector('.msg-content');
    const icon = kind === 'call' ? '⚡' : '✓';
    const color = kind === 'call' ? 'text-cp-warning' : 'text-cp-success';
    const el = document.createElement('div');
    el.className = `flex items-center gap-2 text-xs ${color} py-1 px-2 my-1 bg-cp-bg rounded-lg font-mono`;
    el.innerHTML = `<span>${icon}</span><span class="font-semibold">${escapeHtml(tool)}</span><span class="text-cp-muted truncate">${escapeHtml(detail)}</span>`;
    container.appendChild(el);
    scrollChat();
}

function appendToolSummary(messageEl, toolCalls) {
    if (!toolCalls?.length) return;
    const container = messageEl.querySelector('.msg-content');
    const names = [...new Set(toolCalls.map(t => t.name))];
    const el = document.createElement('div');
    el.className = 'flex items-center gap-2 text-xs text-cp-muted mt-2 pt-2 border-t border-cp-border';
    el.innerHTML = `<i data-lucide="wrench" class="w-3 h-3"></i> Tools used: ${names.join(', ')} (${toolCalls.length} calls)`;
    container.appendChild(el);
    lucide.createIcons();
}

function updateHUD(status, detail) {
    const badge = document.getElementById('status-badge');
    const hudMap = {
        'idle': { text: 'Ready', cls: 'bg-cp-success/20 text-cp-success' },
        'thinking': { text: 'Thinking...', cls: 'bg-cp-accent/20 text-cp-accent' },
        'reasoning': { text: 'Reasoning...', cls: 'bg-cp-accent/20 text-cp-accent' },
        'responding': { text: 'Responding...', cls: 'bg-cp-success/20 text-cp-success' },
        'tool': { text: `Running ${detail}...`, cls: 'bg-cp-warning/20 text-cp-warning' },
        'error': { text: 'Error', cls: 'bg-cp-error/20 text-cp-error' },
    };
    const h = hudMap[status] || hudMap['idle'];
    badge.textContent = h.text;
    badge.className = `text-xs px-2 py-0.5 rounded-full ${h.cls}`;
}

function appendMessage(role, content) {
    const el = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = `${role === 'user' ? 'msg-user' : 'msg-assistant'} rounded-xl p-4 fade-in`;
    const icon = role === 'user' ? 'user' : 'bot';
    const label = role === 'user' ? 'You' : 'Oak';
    div.innerHTML = `
        <div class="flex items-center gap-2 mb-2">
            <i data-lucide="${icon}" class="w-4 h-4 ${role === 'user' ? 'text-cp-accent' : 'text-cp-success'}"></i>
            <span class="text-xs font-semibold ${role === 'user' ? 'text-cp-accent' : 'text-cp-success'}">${label}</span>
        </div>
        <div class="msg-content text-sm leading-relaxed">${content ? marked.parse(content) : ''}</div>
    `;
    el.appendChild(div);
    lucide.createIcons();
    scrollChat();

    if (content) {
        div.querySelectorAll('pre code').forEach(block => hljs.highlightElement(block));
        addCopyButtons(div);
    }
    return div;
}

function addCopyButtons(container) {
    container.querySelectorAll('pre').forEach(pre => {
        if (pre.querySelector('.copy-btn')) return;
        const btn = document.createElement('button');
        btn.className = 'copy-btn absolute top-2 right-2 px-2 py-1 text-xs bg-cp-surface2 text-cp-muted rounded hover:text-cp-text hover:bg-cp-border transition-colors';
        btn.textContent = 'Copy';
        btn.onclick = () => {
            const code = pre.querySelector('code')?.textContent || pre.textContent;
            navigator.clipboard.writeText(code);
            btn.textContent = 'Copied!';
            setTimeout(() => btn.textContent = 'Copy', 1500);
        };
        pre.style.position = 'relative';
        pre.appendChild(btn);
    });
}

function scrollChat() {
    const el = document.getElementById('chat-messages');
    el.scrollTop = el.scrollHeight;
}

// ═══════════════════════════════════════════════════════════════════
// IDE
// ═══════════════════════════════════════════════════════════════════
async function runEditorCode() {
    if (!state.monacoEditor) return;
    const code = state.monacoEditor.getValue();
    const outputPanel = document.getElementById('ide-output');
    const outputContent = document.getElementById('ide-output-content');
    outputPanel.classList.remove('hidden');
    outputContent.textContent = 'Running...\n';

    try {
        const resp = await fetch('/api/code/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code }),
        });
        const result = await resp.json();
        let output = '';
        if (result.stdout) output += result.stdout;
        if (result.stderr) output += '\n' + result.stderr;
        if (result.timed_out) output += '\n⏱ Execution timed out';
        outputContent.textContent = output || '(no output)';
        outputContent.className = result.returncode === 0
            ? 'text-cp-text whitespace-pre-wrap'
            : 'text-cp-error whitespace-pre-wrap';
    } catch (e) {
        outputContent.textContent = `Error: ${e.message}`;
        outputContent.className = 'text-cp-error whitespace-pre-wrap';
    }
}

function clearOutput() {
    document.getElementById('ide-output').classList.add('hidden');
    document.getElementById('ide-output-content').textContent = '';
}

async function askAIAboutCode() {
    if (!state.monacoEditor) return;
    const code = state.monacoEditor.getValue();
    const selected = state.monacoEditor.getModel().getValueInRange(state.monacoEditor.getSelection());
    const codeToAsk = selected || code;

    switchTab('chat');
    const input = document.getElementById('chat-input');
    input.value = `Explain this Python code and suggest improvements:\n\n\`\`\`python\n${codeToAsk}\n\`\`\``;
    sendChat();
}

function openInEditor() {
    const codeEl = document.getElementById('github-file-code');
    const nameEl = document.getElementById('github-file-name');
    if (!codeEl || !state.monacoEditor) return;
    state.monacoEditor.setValue(codeEl.textContent);
    document.getElementById('ide-filename').textContent = nameEl.textContent;
    switchTab('ide');
}

// ═══════════════════════════════════════════════════════════════════
// Wiki
// ═══════════════════════════════════════════════════════════════════
async function loadWikiArticles() {
    try {
        const resp = await fetch('/api/wiki');
        const data = await resp.json();
        renderWikiList(data.articles || []);
        loadWikiTags();
    } catch {}
}

function renderWikiList(articles) {
    const el = document.getElementById('wiki-article-list');
    if (!articles.length) {
        el.innerHTML = '<p class="text-xs text-cp-muted p-2">No articles yet</p>';
        return;
    }
    el.innerHTML = articles.map(a => `
        <div class="sidebar-item flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer text-sm ${a.slug === state.currentWikiSlug ? 'active' : ''}"
             onclick="viewWikiArticle('${a.slug}')">
            <i data-lucide="file-text" class="w-3.5 h-3.5 text-cp-muted flex-shrink-0"></i>
            <span class="truncate">${escapeHtml(a.title)}</span>
        </div>
    `).join('');
    lucide.createIcons();
}

async function loadWikiTags() {
    try {
        const resp = await fetch('/api/wiki/tags');
        const data = await resp.json();
        const el = document.getElementById('wiki-tags');
        el.innerHTML = (data.tags || []).map(t =>
            `<span class="px-2 py-0.5 bg-cp-accent/20 text-cp-accent text-xs rounded-full cursor-pointer hover:bg-cp-accent/30" onclick="filterWikiByTag('${t}')">${t}</span>`
        ).join('');
    } catch {}
}

async function filterWikiByTag(tag) {
    try {
        const resp = await fetch(`/api/wiki?tag=${encodeURIComponent(tag)}`);
        const data = await resp.json();
        renderWikiList(data.articles || []);
    } catch {}
}

async function searchWiki(query) {
    if (!query.trim()) return loadWikiArticles();
    try {
        const resp = await fetch(`/api/wiki/search/${encodeURIComponent(query)}`);
        const data = await resp.json();
        const el = document.getElementById('wiki-article-list');
        if (!data.results?.length) {
            el.innerHTML = '<p class="text-xs text-cp-muted p-2">No results</p>';
            return;
        }
        el.innerHTML = data.results.map(r => `
            <div class="sidebar-item flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer text-sm"
                 onclick="viewWikiArticle('${r.source}')">
                <i data-lucide="search" class="w-3.5 h-3.5 text-cp-muted flex-shrink-0"></i>
                <div class="truncate">
                    <div class="truncate">${escapeHtml(r.title || r.source)}</div>
                    <div class="text-xs text-cp-muted">${(r.score * 100).toFixed(0)}% match</div>
                </div>
            </div>
        `).join('');
        lucide.createIcons();
    } catch {}
}

async function viewWikiArticle(slug) {
    try {
        const [articleResp, htmlResp] = await Promise.all([
            fetch(`/api/wiki/${slug}`),
            fetch(`/api/wiki/${slug}/html`),
        ]);
        const article = await articleResp.json();
        const htmlData = await htmlResp.json();

        state.currentWikiSlug = slug;
        document.getElementById('wiki-welcome').classList.add('hidden');
        document.getElementById('wiki-article-view').classList.remove('hidden');
        document.getElementById('wiki-editor').classList.add('hidden');
        document.getElementById('wiki-viewer').classList.remove('hidden');

        document.getElementById('wiki-view-title').textContent = article.title;
        document.getElementById('wiki-view-content').innerHTML = htmlData.html;
        document.getElementById('wiki-view-tags').innerHTML = (article.tags || []).map(t =>
            `<span class="px-2 py-0.5 bg-cp-accent/20 text-cp-accent text-xs rounded-full">${t}</span>`
        ).join('');

        // Highlight code blocks
        document.querySelectorAll('#wiki-view-content pre code').forEach(block => hljs.highlightElement(block));
        loadWikiArticles();
    } catch {}
}

function showWikiEditor(slug) {
    state.editingWikiSlug = slug || null;
    document.getElementById('wiki-viewer').classList.add('hidden');
    document.getElementById('wiki-editor').classList.remove('hidden');

    if (slug) {
        fetch(`/api/wiki/${slug}`).then(r => r.json()).then(article => {
            document.getElementById('wiki-edit-title').value = article.title;
            document.getElementById('wiki-edit-content').value = article.content;
            document.getElementById('wiki-edit-tags').value = (article.tags || []).join(', ');
        });
    } else {
        document.getElementById('wiki-edit-title').value = '';
        document.getElementById('wiki-edit-content').value = '';
        document.getElementById('wiki-edit-tags').value = '';
    }
}

async function saveWikiArticle() {
    const title = document.getElementById('wiki-edit-title').value.trim();
    const content = document.getElementById('wiki-edit-content').value;
    const tags = document.getElementById('wiki-edit-tags').value.split(',').map(t => t.trim()).filter(Boolean);

    if (!title) { toast('Title is required', 'error'); return; }

    try {
        if (state.editingWikiSlug) {
            await fetch(`/api/wiki/${state.editingWikiSlug}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, content, tags }),
            });
            toast('Article updated', 'success');
            viewWikiArticle(state.editingWikiSlug);
        } else {
            const resp = await fetch('/api/wiki', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, content, tags }),
            });
            const article = await resp.json();
            toast('Article created', 'success');
            viewWikiArticle(article.slug);
        }
    } catch (e) {
        toast(`Error: ${e.message}`, 'error');
    }
}

function cancelWikiEdit() {
    document.getElementById('wiki-editor').classList.add('hidden');
    document.getElementById('wiki-viewer').classList.remove('hidden');
}

function editCurrentArticle() {
    if (state.currentWikiSlug) showWikiEditor(state.currentWikiSlug);
}

async function deleteCurrentArticle() {
    if (!state.currentWikiSlug) return;
    if (!confirm('Delete this article?')) return;
    await fetch(`/api/wiki/${state.currentWikiSlug}`, { method: 'DELETE' });
    state.currentWikiSlug = null;
    document.getElementById('wiki-article-view').classList.add('hidden');
    document.getElementById('wiki-welcome').classList.remove('hidden');
    toast('Article deleted', 'success');
    loadWikiArticles();
}

async function reindexWiki() {
    try {
        const resp = await fetch('/api/wiki/reindex', { method: 'POST' });
        const data = await resp.json();
        toast(`Re-indexed ${data.indexed} articles`, 'success');
    } catch {}
}

// ═══════════════════════════════════════════════════════════════════
// GitHub
// ═══════════════════════════════════════════════════════════════════
async function loadLocalRepos() {
    try {
        const resp = await fetch('/api/github/repos/local');
        const data = await resp.json();
        const el = document.getElementById('local-repo-list');
        const repos = data.repos || [];
        if (!repos.length) {
            el.innerHTML = '<p class="text-xs text-cp-muted p-2">No cloned repos</p>';
            return;
        }
        el.innerHTML = repos.map(r => `
            <div class="sidebar-item flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer text-sm"
                 onclick="browseRepo('${r.name}')">
                <i data-lucide="folder-git-2" class="w-3.5 h-3.5 text-cp-muted flex-shrink-0"></i>
                <div class="truncate flex-1">
                    <div class="truncate">${escapeHtml(r.name)}</div>
                    <div class="text-xs text-cp-muted">${escapeHtml(r.branch)}</div>
                </div>
            </div>
        `).join('');
        lucide.createIcons();
    } catch {}
}

function showCloneModal() {
    document.getElementById('clone-modal').classList.remove('hidden');
    document.getElementById('clone-url').value = '';
    document.getElementById('clone-name').value = '';
    document.getElementById('clone-status').textContent = '';
}

function hideCloneModal() {
    document.getElementById('clone-modal').classList.add('hidden');
}

async function cloneRepo() {
    const url = document.getElementById('clone-url').value.trim();
    const name = document.getElementById('clone-name').value.trim() || undefined;
    const status = document.getElementById('clone-status');

    if (!url) { status.textContent = 'URL is required'; return; }
    status.textContent = 'Cloning...';

    try {
        const resp = await fetch('/api/github/repos/clone', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, name }),
        });
        const data = await resp.json();
        if (data.status === 'cloned') {
            toast(`Cloned ${data.name}`, 'success');
            hideCloneModal();
            loadLocalRepos();
        } else if (data.status === 'exists') {
            status.textContent = 'Repo already exists locally';
        } else {
            status.textContent = `Error: ${data.error || 'Unknown error'}`;
        }
    } catch (e) {
        status.textContent = `Error: ${e.message}`;
    }
}

async function browseRepo(name, path = '') {
    state.currentRepo = name;
    state.currentRepoPath = path;

    document.getElementById('github-welcome').classList.add('hidden');
    document.getElementById('github-repo-view').classList.remove('hidden');
    document.getElementById('github-repo-name').textContent = name;
    document.getElementById('github-repo-path').textContent = path ? `/ ${path}` : '';
    document.getElementById('github-file-content').classList.add('hidden');

    try {
        const resp = await fetch(`/api/github/repos/${name}/browse?path=${encodeURIComponent(path)}`);
        const data = await resp.json();
        const browser = document.getElementById('github-file-browser');

        if (!data.files?.length) {
            browser.innerHTML = '<p class="p-4 text-sm text-cp-muted">Empty directory</p>';
            return;
        }

        let html = '';
        // Add parent directory link
        if (path) {
            const parent = path.split('/').slice(0, -1).join('/');
            html += `<div class="flex items-center gap-3 px-4 py-2 hover:bg-cp-surface2 cursor-pointer border-b border-cp-border" onclick="browseRepo('${name}', '${parent}')">
                <i data-lucide="arrow-up" class="w-4 h-4 text-cp-muted"></i>
                <span class="text-sm">..</span>
            </div>`;
        }

        data.files.forEach(f => {
            const icon = f.type === 'dir' ? 'folder' : 'file-code';
            const color = f.type === 'dir' ? 'text-cp-warning' : 'text-cp-muted';
            const click = f.type === 'dir'
                ? `browseRepo('${name}', '${f.path}')`
                : `viewRepoFile('${name}', '${f.path}')`;
            html += `<div class="flex items-center gap-3 px-4 py-2 hover:bg-cp-surface2 cursor-pointer border-b border-cp-border" onclick="${click}">
                <i data-lucide="${icon}" class="w-4 h-4 ${color}"></i>
                <span class="text-sm flex-1">${escapeHtml(f.name)}</span>
                ${f.size ? `<span class="text-xs text-cp-muted">${formatSize(f.size)}</span>` : ''}
            </div>`;
        });

        browser.innerHTML = html;
        lucide.createIcons();
    } catch {}
}

async function viewRepoFile(repo, path) {
    try {
        const resp = await fetch(`/api/github/repos/${repo}/file?path=${encodeURIComponent(path)}`);
        const data = await resp.json();

        document.getElementById('github-file-content').classList.remove('hidden');
        document.getElementById('github-file-name').textContent = path;
        const codeEl = document.getElementById('github-file-code');
        codeEl.textContent = data.content;

        const ext = path.split('.').pop();
        if (['py', 'js', 'ts', 'json', 'html', 'css', 'md', 'yml', 'yaml', 'toml', 'sh', 'bash'].includes(ext)) {
            const lang = ext === 'py' ? 'python' : ext;
            if (hljs.getLanguage(lang)) {
                codeEl.innerHTML = hljs.highlight(data.content, { language: lang }).value;
            }
        }
    } catch {}
}

function githubBack() {
    if (state.currentRepoPath) {
        const parent = state.currentRepoPath.split('/').slice(0, -1).join('/');
        browseRepo(state.currentRepo, parent);
    } else {
        document.getElementById('github-repo-view').classList.add('hidden');
        document.getElementById('github-welcome').classList.remove('hidden');
        state.currentRepo = null;
    }
}

async function pullCurrentRepo() {
    if (!state.currentRepo) return;
    const resp = await fetch(`/api/github/repos/${state.currentRepo}/pull`, { method: 'POST' });
    const data = await resp.json();
    if (data.status === 'pulled') {
        toast(`Pulled latest for ${state.currentRepo}`, 'success');
        browseRepo(state.currentRepo, state.currentRepoPath);
    } else {
        toast(`Pull failed: ${data.error || data.status}`, 'error');
    }
}

async function indexCurrentRepo() {
    if (!state.currentRepo) return;
    toast('Indexing repo...', 'info');
    try {
        const resp = await fetch(`/api/github/repos/${state.currentRepo}/index`, { method: 'POST' });
        const data = await resp.json();
        toast(`Indexed ${data.indexed_files}/${data.total_python_files} Python files`, 'success');
    } catch (e) {
        toast(`Indexing failed: ${e.message}`, 'error');
    }
}

// ═══════════════════════════════════════════════════════════════════
// Joplin Notes
// ═══════════════════════════════════════════════════════════════════
let currentNoteId = null;
let editingNoteId = null;

async function loadJoplinNotes() {
    try {
        const statusResp = await fetch('/api/joplin/ping');
        const status = await statusResp.json();
        if (!status.connected || !status.configured) {
            document.getElementById('notes-welcome').classList.remove('hidden');
            document.getElementById('notes-note-view').classList.add('hidden');
            document.getElementById('notes-list').innerHTML = '<p class="text-xs text-cp-muted p-2">Connect Joplin first</p>';
            return;
        }
        document.getElementById('joplin-setup').classList.add('hidden');
        await Promise.all([loadNotebooks(), loadNotesList()]);
    } catch {
        document.getElementById('notes-list').innerHTML = '<p class="text-xs text-cp-muted p-2">Cannot reach Joplin</p>';
    }
}

async function loadNotebooks() {
    try {
        const resp = await fetch('/api/joplin/notebooks');
        const data = await resp.json();
        const select = document.getElementById('notebook-select');
        const editorSelect = document.getElementById('note-edit-notebook');
        const opts = '<option value="">All Notes</option>' +
            (data.notebooks || []).map(nb => `<option value="${nb.id}">${escapeHtml(nb.title)}</option>`).join('');
        select.innerHTML = opts;
        editorSelect.innerHTML = opts.replace('All Notes', 'Oak notebook');
    } catch {}
}

async function loadNotesList(notebookId) {
    try {
        let url = '/api/joplin/notes?limit=50';
        if (notebookId) {
            url = `/api/joplin/notebooks/${notebookId}/notes`;
        }
        const resp = await fetch(url);
        const data = await resp.json();
        const notes = data.notes || [];
        const el = document.getElementById('notes-list');
        if (!notes.length) {
            el.innerHTML = '<p class="text-xs text-cp-muted p-2">No notes</p>';
            return;
        }
        el.innerHTML = notes.map(n => {
            const active = n.id === currentNoteId ? 'active' : '';
            const date = n.updated_time ? new Date(n.updated_time).toLocaleDateString() : '';
            return `<div class="sidebar-item flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer text-sm ${active}" onclick="viewJoplinNote('${n.id}')">
                <i data-lucide="file-text" class="w-3.5 h-3.5 text-cp-muted flex-shrink-0"></i>
                <div class="truncate flex-1">
                    <div class="truncate">${escapeHtml(n.title)}</div>
                    <div class="text-xs text-cp-muted">${date}</div>
                </div>
            </div>`;
        }).join('');
        lucide.createIcons();
    } catch {}
}

function loadNotebookNotes(notebookId) {
    loadNotesList(notebookId || undefined);
}

async function searchJoplinNotes(query) {
    if (!query.trim()) return loadNotesList();
    try {
        const resp = await fetch(`/api/joplin/search?q=${encodeURIComponent(query)}`);
        const data = await resp.json();
        const el = document.getElementById('notes-list');
        const notes = data.notes || [];
        if (!notes.length) {
            el.innerHTML = '<p class="text-xs text-cp-muted p-2">No results</p>';
            return;
        }
        el.innerHTML = notes.map(n => `<div class="sidebar-item flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer text-sm" onclick="viewJoplinNote('${n.id}')">
            <i data-lucide="search" class="w-3.5 h-3.5 text-cp-muted flex-shrink-0"></i>
            <span class="truncate">${escapeHtml(n.title)}</span>
        </div>`).join('');
        lucide.createIcons();
    } catch {}
}

async function viewJoplinNote(noteId) {
    try {
        const resp = await fetch(`/api/joplin/notes/${noteId}`);
        const data = await resp.json();
        currentNoteId = noteId;

        document.getElementById('notes-welcome').classList.add('hidden');
        document.getElementById('notes-note-view').classList.remove('hidden');
        document.getElementById('notes-editor').classList.add('hidden');
        document.getElementById('notes-viewer').classList.remove('hidden');

        // Joplin returns markdown content - parse it
        const content = data.body || '';
        document.getElementById('note-view-title').textContent = data.title;
        document.getElementById('note-view-content').innerHTML = marked.parse(content);
        document.getElementById('note-view-tags').innerHTML = '';

        // Highlight code blocks
        document.querySelectorAll('#note-view-content pre code').forEach(block => hljs.highlightElement(block));
    } catch {}
}

function showNoteEditor(noteId) {
    editingNoteId = null;
    document.getElementById('notes-viewer').classList.add('hidden');
    document.getElementById('notes-editor').classList.remove('hidden');
    document.getElementById('note-edit-title').value = '';
    document.getElementById('note-edit-body').value = '';
}

async function saveJoplinNote() {
    const title = document.getElementById('note-edit-title').value.trim();
    const body = document.getElementById('note-edit-body').value;
    const notebookId = document.getElementById('note-edit-notebook').value;
    if (!title) { toast('Title is required', 'error'); return; }

    try {
        const resp = await fetch('/api/joplin/notes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, body, notebook_id: notebookId }),
        });
        const note = await resp.json();
        toast('Note created in Joplin', 'success');
        cancelNoteEdit();
        loadNotesList();
    } catch (e) {
        toast(`Error: ${e.message}`, 'error');
    }
}

function cancelNoteEdit() {
    document.getElementById('notes-editor').classList.add('hidden');
    document.getElementById('notes-viewer').classList.remove('hidden');
}

function editCurrentNote() {
    if (currentNoteId) showNoteEditor();
}

async function deleteCurrentNote() {
    if (!currentNoteId || !confirm('Delete this note?')) return;
    await fetch(`/api/joplin/notes/${currentNoteId}`, { method: 'DELETE' });
    currentNoteId = null;
    document.getElementById('notes-note-view').classList.add('hidden');
    document.getElementById('notes-welcome').classList.remove('hidden');
    toast('Note deleted', 'success');
    loadNotesList();
}

async function syncNoteToWiki() {
    if (!currentNoteId) return;
    try {
        const resp = await fetch(`/api/joplin/notes/${currentNoteId}/to-wiki`, { method: 'POST' });
        const data = await resp.json();
        toast(`Synced to wiki: ${data.slug}`, 'success');
    } catch (e) {
        toast('Sync failed', 'error');
    }
}

function askAIAboutNote() {
    if (!currentNoteId) return;
    const title = document.getElementById('note-view-title').textContent;
    const content = document.getElementById('note-view-content').innerText.slice(0, 500);
    switchTab('chat');
    const input = document.getElementById('chat-input');
    input.value = `Review and expand on this note titled "${title}":\n\n${content}`;
    sendChat();
}

async function saveJoplinToken(token) {
    if (!token) return;
    try {
        const resp = await fetch('/api/joplin/token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token }),
        });
        const data = await resp.json();
        const statusEl = document.getElementById('joplin-connect-status');
        if (data.success) {
            statusEl.textContent = 'Connected to Joplin!';
            statusEl.className = 'text-xs text-cp-success';
            toast('Connected to Joplin', 'success');
            loadJoplinNotes();
        } else {
            statusEl.textContent = data.error || 'Connection failed';
            statusEl.className = 'text-xs text-cp-error';
        }
    } catch (e) {
        document.getElementById('joplin-connect-status').textContent = `Error: ${e.message}`;
    }
}

// ═══════════════════════════════════════════════════════════════════
// Settings
// ═══════════════════════════════════════════════════════════════════
async function loadSettings() {
    try {
        const [hwResp, healthResp, ghResp] = await Promise.all([
            fetch('/api/hardware'),
            fetch('/api/health'),
            fetch('/api/github/status'),
        ]);
        const hw = await hwResp.json();
        const health = await healthResp.json();
        const gh = await ghResp.json();

        document.getElementById('hardware-info').innerHTML = `
            <div><span class="text-cp-muted">RAM:</span> ${hw.ram_gb} GB</div>
            <div><span class="text-cp-muted">CPUs:</span> ${hw.cpu_count}</div>
            <div><span class="text-cp-muted">Platform:</span> ${hw.platform}</div>
            <div><span class="text-cp-muted">GPU:</span> ${hw.gpu?.available ? hw.gpu.name + ' (' + hw.gpu.vram_gb + 'GB)' : 'None detected'}</div>
            <div><span class="text-cp-muted">Recommended:</span> ${hw.recommended_model}</div>
        `;

        const kb = health.knowledge_base || {};
        document.getElementById('kb-stats').textContent = `Wiki chunks: ${kb.wiki_chunks || 0} | Code chunks: ${kb.code_chunks || 0}`;

        document.getElementById('github-auth-status').textContent = gh.authenticated
            ? '✓ Authenticated' : 'Not authenticated — enter a token to access private repos';

        loadModels();
    } catch {}
}

async function saveGitHubToken() {
    const token = document.getElementById('github-token-input').value.trim();
    if (!token) return;
    await fetch('/api/github/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
    });
    document.getElementById('github-auth-status').textContent = '✓ Authenticated';
    document.getElementById('github-token-input').value = '';
    toast('GitHub token saved', 'success');
}

// ═══════════════════════════════════════════════════════════════════
// Utilities
// ═══════════════════════════════════════════════════════════════════
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function formatSize(bytes) {
    if (!bytes) return '';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    let size = bytes;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
    return `${size.toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

function toast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const colors = {
        success: 'bg-cp-success/20 text-cp-success border-cp-success/30',
        error: 'bg-cp-error/20 text-cp-error border-cp-error/30',
        info: 'bg-cp-accent/20 text-cp-accent border-cp-accent/30',
        warning: 'bg-cp-warning/20 text-cp-warning border-cp-warning/30',
    };
    const div = document.createElement('div');
    div.className = `px-4 py-2.5 rounded-xl border text-sm fade-in ${colors[type] || colors.info}`;
    div.textContent = message;
    container.appendChild(div);
    setTimeout(() => div.remove(), 4000);
}
