// ============================================================================
// Kyky — frontend
// ============================================================================

let token = localStorage.getItem('kykyToken') || null;
let currentUser = null;
let sessionId = null;
let isRegisterMode = false;
let brand = { ai_name: 'Kyky', icon_url: '/static/icon.png' };
let pendingAttachments = []; // {type, name, data_url?, extracted_text?}

const $ = (sel) => document.querySelector(sel);

// ---------- Marca (nome + ícone) aplicada em qualquer tela ----------

function applyBrandToOrb(orbEl) {
  orbEl.innerHTML = '';
  const img = document.createElement('img');
  img.src = brand.icon_url + (brand.icon_url.includes('?') ? '' : `?t=${Date.now() % 100000}`);
  img.onload = () => img.classList.add('loaded');
  img.onerror = () => img.remove();
  orbEl.appendChild(img);
}

function applyBrandEverywhere() {
  document.title = brand.ai_name;
  document.querySelectorAll('.brand-name').forEach((el) => (el.textContent = brand.ai_name));
  document.querySelectorAll('[data-brand-orb]').forEach(applyBrandToOrb);
}

async function loadPublicConfig() {
  try {
    const res = await fetch('/config/public');
    if (res.ok) brand = await res.json();
  } catch (e) { /* usa fallback */ }
  applyBrandEverywhere();
}

// ---------- Autenticação ----------

const authScreen = $('#authScreen');
const appScreen = $('#appScreen');
const authUser = $('#authUser');
const authPass = $('#authPass');
const authSubmit = $('#authSubmit');
const authError = $('#authError');
const authSub = $('#authSub');
const toggleLink = $('#toggleLink');
const toggleText = $('#toggleText');

toggleLink.addEventListener('click', () => {
  isRegisterMode = !isRegisterMode;
  authSubmit.textContent = isRegisterMode ? 'Cadastrar' : 'Entrar';
  authSub.textContent = isRegisterMode
    ? 'Crie sua conta (a primeira conta criada vira admin)'
    : 'Entre com sua conta';
  toggleText.innerHTML = isRegisterMode
    ? 'Já tem conta? <a id="toggleLink2">Entrar</a>'
    : 'Não tem conta? <a id="toggleLink2">Cadastre-se</a>';
  $('#toggleLink2').addEventListener('click', () => toggleLink.click());
  authError.textContent = '';
});

authSubmit.addEventListener('click', async () => {
  const username = authUser.value.trim();
  const password = authPass.value;
  authError.textContent = '';
  if (!username || !password) {
    authError.textContent = 'Preencha usuário e senha.';
    return;
  }
  const endpoint = isRegisterMode ? '/register' : '/login';
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      authError.textContent = data.detail || 'Erro ao autenticar.';
      return;
    }
    token = data.token;
    currentUser = { username: data.username, role: data.role };
    localStorage.setItem('kykyToken', token);
    showApp();
  } catch (err) {
    authError.textContent = 'Erro de conexão: ' + err.message;
  }
});

[authUser, authPass].forEach((el) =>
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') authSubmit.click();
  })
);

function showAuth() {
  authScreen.style.display = 'flex';
  appScreen.style.display = 'none';
}

async function showApp() {
  authScreen.style.display = 'none';
  appScreen.style.display = 'flex';
  $('#whoami').innerHTML =
    currentUser.username + (currentUser.role === 'admin' ? ' <span class="badge">admin</span>' : '');
  $('#adminPanelBtn').style.display = currentUser.role === 'admin' ? 'block' : 'none';
  applyBrandEverywhere();
  await loadSessions();
  clearChatView();
}

$('#logoutBtn').addEventListener('click', () => {
  token = null;
  currentUser = null;
  localStorage.removeItem('kykyToken');
  showAuth();
});

// ---------- Sidebar / sessões ----------

const sessionListEl = $('#sessionList');

async function loadSessions() {
  try {
    const res = await fetch('/sessions', { headers: authHeaders() });
    if (!res.ok) return;
    const sessions = await res.json();
    renderSessionList(sessions);
  } catch (e) { /* ignore */ }
}

function timeAgo(iso) {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'agora';
  if (mins < 60) return `${mins}min`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  return `${days}d`;
}

function renderSessionList(sessions) {
  sessionListEl.innerHTML = '';
  if (!sessions.length) {
    sessionListEl.innerHTML = '<div class="session-empty">Suas conversas aparecem aqui. Comece uma nova!</div>';
    return;
  }
  for (const s of sessions) {
    const row = document.createElement('div');
    row.className = 'session-item' + (s.session_id === sessionId ? ' active' : '');
    row.dataset.id = s.session_id;

    const title = document.createElement('span');
    title.className = 'title';
    title.textContent = s.title || 'Nova conversa';
    title.title = `Atualizado há ${timeAgo(s.updated_at)}`;

    const actions = document.createElement('div');
    actions.className = 'session-actions';

    const renameBtn = document.createElement('button');
    renameBtn.textContent = '✎';
    renameBtn.title = 'Renomear';
    renameBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      startRename(row, s);
    });

    const delBtn = document.createElement('button');
    delBtn.textContent = '🗑';
    delBtn.title = 'Excluir';
    delBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('Excluir esta conversa? Isso não pode ser desfeito.')) return;
      await fetch('/history/' + s.session_id, { method: 'DELETE', headers: authHeaders() });
      if (sessionId === s.session_id) {
        sessionId = null;
        clearChatView();
      }
      loadSessions();
    });

    actions.append(renameBtn, delBtn);
    row.append(title, actions);
    row.addEventListener('click', () => openSession(s.session_id, s.title));
    sessionListEl.appendChild(row);
  }
}

function startRename(row, s) {
  row.innerHTML = '';
  const input = document.createElement('input');
  input.className = 'rename-input';
  input.value = s.title;
  row.appendChild(input);
  input.focus();
  input.select();
  const commit = async () => {
    const newTitle = input.value.trim();
    if (newTitle && newTitle !== s.title) {
      await fetch('/sessions/' + s.session_id, {
        method: 'PATCH',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: newTitle }),
      });
    }
    loadSessions();
  };
  input.addEventListener('blur', commit);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') input.blur();
    if (e.key === 'Escape') loadSessions();
  });
}

async function openSession(id, title) {
  sessionId = id;
  $('#chatTitle').textContent = title || 'Conversa';
  closeSidebarMobile();
  document.querySelectorAll('.session-item').forEach((el) =>
    el.classList.toggle('active', el.dataset.id === id)
  );
  await loadHistory();
}

$('#newChatBtn').addEventListener('click', () => {
  sessionId = null;
  clearChatView();
  closeSidebarMobile();
});

function clearChatView() {
  $('#chatTitle').textContent = 'Nova conversa';
  chatEl.innerHTML = `
    <div class="empty-state">
      <div class="orb pulse orb--lg" data-brand-orb></div>
      <h2>Fala com a ${brand.ai_name}</h2>
      <p>Manda uma mensagem, uma imagem ou um PDF. Ela lembra da conversa e você pode voltar a qualquer sessão pela barra lateral.</p>
    </div>`;
  applyBrandEverywhere();
  document.querySelectorAll('.session-item').forEach((el) => el.classList.remove('active'));
}

// ---------- Sidebar mobile ----------

$('#menuToggle').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
function closeSidebarMobile() {
  if (window.innerWidth <= 760) $('#sidebar').classList.remove('open');
}

// ---------- Chat ----------

const chatEl = $('#chat');
const inputEl = $('#input');
const sendBtn = $('#sendBtn');

function authHeaders() {
  return { Authorization: 'Bearer ' + token };
}

function renderContentToHtml(text) {
  // Escapa HTML e formata blocos de código simples (```...```) e `inline`.
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  let safe = esc(text);
  safe = safe.replace(/```([\s\S]*?)```/g, (_, code) => `<pre>${code}</pre>`);
  safe = safe.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
  return safe;
}

function addMessage({ who, text, attachments = [], pending = false }) {
  const row = document.createElement('div');
  row.className = 'msg-row ' + who;

  const orb = document.createElement('div');
  orb.className = 'orb orb--sm';
  if (who === 'ai') {
    orb.setAttribute('data-brand-orb', '');
  } else {
    orb.style.background = 'linear-gradient(135deg, #4b4664, #6f6789)';
  }

  const bubble = document.createElement('div');
  bubble.className = 'msg' + (pending ? ' pending' : '');
  bubble.innerHTML = renderContentToHtml(text);

  for (const att of attachments) {
    if (att.type === 'image' && att.data_url) {
      const img = document.createElement('img');
      img.className = 'attachment-img-preview';
      img.src = att.data_url;
      bubble.appendChild(img);
    } else if (att.type === 'texto') {
      const chip = document.createElement('div');
      chip.className = 'attachment-chip';
      chip.textContent = '📄 ' + att.name;
      bubble.appendChild(chip);
    }
  }

  row.append(orb, bubble);
  chatEl.appendChild(row);
  if (who === 'ai') applyBrandToOrb(orb);
  chatEl.scrollTop = chatEl.scrollHeight;
  return bubble;
}

async function send() {
  const text = inputEl.value.trim();
  if (!text && pendingAttachments.length === 0) return;
  inputEl.value = '';
  autoGrow();
  sendBtn.disabled = true;

  if (chatEl.querySelector('.empty-state')) chatEl.innerHTML = '';

  addMessage({ who: 'user', text, attachments: pendingAttachments });
  const pendingBubble = addMessage({ who: 'ai', text: 'pensando...', pending: true });

  const attachmentsToSend = pendingAttachments;
  pendingAttachments = [];
  renderPendingAttachments();

  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message: text, attachments: attachmentsToSend }),
    });
    const data = await res.json();

    if (res.status === 401) {
      showAuth();
      return;
    }
    if (!res.ok) {
      pendingBubble.textContent = 'Erro: ' + (data.detail || 'algo deu errado');
      pendingBubble.classList.remove('pending');
      sendBtn.disabled = false;
      return;
    }

    const isNewSession = !sessionId;
    sessionId = data.session_id;
    pendingBubble.innerHTML = renderContentToHtml(data.reply);
    pendingBubble.classList.remove('pending');

    if (isNewSession) $('#chatTitle').textContent = text.slice(0, 42) || 'Conversa';
    loadSessions();
  } catch (err) {
    pendingBubble.textContent = 'Erro de conexão: ' + err.message;
    pendingBubble.classList.remove('pending');
  }
  sendBtn.disabled = false;
  inputEl.focus();
}

sendBtn.addEventListener('click', send);
inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
function autoGrow() {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + 'px';
}
inputEl.addEventListener('input', autoGrow);

async function loadHistory() {
  chatEl.innerHTML = '';
  if (!sessionId) return;
  try {
    const res = await fetch('/history/' + sessionId, { headers: authHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    for (const m of data.history) {
      const text = typeof m.content === 'string' ? m.content : (m.content.find((c) => c.type === 'text')?.text || '');
      addMessage({ who: m.role === 'user' ? 'user' : 'ai', text, attachments: m.attachments || [] });
    }
  } catch (e) { /* sessão nova, ignora */ }
}

// ---------- Anexos ----------

const attachInput = $('#attachInput');
$('#attachBtn').addEventListener('click', () => attachInput.click());

attachInput.addEventListener('change', async () => {
  const files = Array.from(attachInput.files || []);
  attachInput.value = '';
  for (const file of files) {
    if (file.size > 15 * 1024 * 1024) {
      alert(`"${file.name}" é maior que 15MB.`);
      continue;
    }
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await fetch('/upload', { method: 'POST', headers: authHeaders(), body: formData });
      const data = await res.json();
      if (!res.ok) {
        alert(data.detail || 'Erro ao enviar arquivo.');
        continue;
      }
      pendingAttachments.push(data);
      renderPendingAttachments();
    } catch (e) {
      alert('Erro de conexão ao enviar arquivo.');
    }
  }
});

function renderPendingAttachments() {
  const wrap = $('#pendingAttachments');
  wrap.innerHTML = '';
  pendingAttachments.forEach((att, idx) => {
    const chip = document.createElement('div');
    chip.className = 'pending-att';
    if (att.type === 'image') {
      const img = document.createElement('img');
      img.src = att.data_url;
      chip.appendChild(img);
    }
    const label = document.createElement('span');
    label.textContent = att.name.length > 20 ? att.name.slice(0, 18) + '…' : att.name;
    const rm = document.createElement('button');
    rm.textContent = '✕';
    rm.addEventListener('click', () => {
      pendingAttachments.splice(idx, 1);
      renderPendingAttachments();
    });
    chip.append(label, rm);
    wrap.appendChild(chip);
  });
}

// ============================================================================
// Painel de administração
// ============================================================================

const adminOverlay = $('#adminOverlay');
$('#adminPanelBtn').addEventListener('click', () => openAdmin());
$('#adminClose').addEventListener('click', closeAdmin);
adminOverlay.addEventListener('click', (e) => { if (e.target === adminOverlay) closeAdmin(); });

function openAdmin() {
  adminOverlay.classList.add('open');
  switchAdminTab('overview');
}
function closeAdmin() {
  adminOverlay.classList.remove('open');
}

document.querySelectorAll('.admin-tab').forEach((btn) =>
  btn.addEventListener('click', () => switchAdminTab(btn.dataset.tab))
);

function switchAdminTab(tab) {
  document.querySelectorAll('.admin-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.admin-panel').forEach((p) => p.classList.toggle('active', p.id === 'panel-' + tab));
  if (tab === 'overview') loadAdminStats();
  if (tab === 'personality') loadAdminConfig();
  if (tab === 'users') loadAdminUsers();
  if (tab === 'suggestions') loadAdminSuggestions();
}

// ---- Visão geral ----

async function loadAdminStats() {
  const el = $('#panel-overview');
  el.innerHTML = '<div class="empty-admin">Carregando...</div>';
  try {
    const res = await fetch('/admin/stats', { headers: authHeaders() });
    const s = await res.json();
    const maxMsg = Math.max(1, ...s.messages_by_day.map((d) => d.messages));
    el.innerHTML = `
      <div class="stat-grid">
        <div class="stat-card"><div class="num">${s.total_users}</div><div class="label">Usuários</div></div>
        <div class="stat-card"><div class="num">${s.active_24h}</div><div class="label">Ativos (24h)</div></div>
        <div class="stat-card"><div class="num">${s.active_7d}</div><div class="label">Ativos (7 dias)</div></div>
        <div class="stat-card"><div class="num">${s.total_sessions}</div><div class="label">Conversas</div></div>
        <div class="stat-card"><div class="num">${s.total_messages}</div><div class="label">Mensagens totais</div></div>
        <div class="stat-card"><div class="num">${s.pending_suggestions}</div><div class="label">Sugestões pendentes</div></div>
      </div>
      <div>
        <div class="admin-field"><label>Mensagens por dia (últimos 14 dias)</label></div>
        <div class="bar-chart">
          ${s.messages_by_day.map((d) => `<div class="bar" style="height:${Math.max(4, (d.messages / maxMsg) * 90)}px" title="${d.day}: ${d.messages}"></div>`).join('') || '<div class="empty-admin">Sem dados ainda.</div>'}
        </div>
      </div>
    `;
  } catch (e) {
    el.innerHTML = '<div class="empty-admin">Erro ao carregar estatísticas.</div>';
  }
}

// ---- Personalidade / config ----

async function loadAdminConfig() {
  try {
    const res = await fetch('/admin/config', { headers: authHeaders() });
    const cfg = await res.json();
    $('#cfgName').value = cfg.ai_name;
    $('#cfgNotes').value = cfg.personality_notes || '';
    $('#cfgModel').value = cfg.model;
  } catch (e) { /* ignore */ }
}

$('#cfgSaveBtn').addEventListener('click', async () => {
  const body = {
    ai_name: $('#cfgName').value,
    personality_notes: $('#cfgNotes').value,
    model: $('#cfgModel').value,
  };
  const res = await fetch('/admin/config', {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.ok) {
    await loadPublicConfig();
    $('#cfgSavedMsg').textContent = 'Salvo!';
    setTimeout(() => ($('#cfgSavedMsg').textContent = ''), 1800);
  }
});

$('#cfgResetBtn').addEventListener('click', async () => {
  if (!confirm('Restaurar nome e personalidade para o padrão de fábrica?')) return;
  await fetch('/admin/config/reset', { method: 'POST', headers: authHeaders() });
  await loadAdminConfig();
  await loadPublicConfig();
});

$('#iconInput').addEventListener('change', async () => {
  const file = $('#iconInput').files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch('/admin/icon', { method: 'POST', headers: authHeaders(), body: formData });
  if (res.ok) {
    await loadPublicConfig();
    $('#iconPreviewMsg').textContent = 'Ícone atualizado!';
    setTimeout(() => ($('#iconPreviewMsg').textContent = ''), 1800);
  } else {
    const data = await res.json();
    alert(data.detail || 'Erro ao enviar ícone.');
  }
});

// ---- Usuários ----

async function loadAdminUsers() {
  const el = $('#panel-users');
  el.innerHTML = '<div class="empty-admin">Carregando...</div>';
  try {
    const res = await fetch('/admin/users', { headers: authHeaders() });
    const users = await res.json();
    el.innerHTML = users.map((u) => `
      <div class="list-item">
        <div>
          <div>${u.username} ${u.role === 'admin' ? '<span class="badge">admin</span>' : ''}</div>
          <div class="meta">desde ${new Date(u.created_at).toLocaleDateString('pt-BR')}</div>
        </div>
        ${u.role !== 'admin' ? `<button class="danger-btn" data-user="${u.username}">Remover</button>` : ''}
      </div>
    `).join('') || '<div class="empty-admin">Nenhum usuário ainda.</div>';

    el.querySelectorAll('.danger-btn').forEach((btn) =>
      btn.addEventListener('click', async () => {
        if (!confirm(`Remover o acesso de "${btn.dataset.user}"?`)) return;
        await fetch('/admin/users/' + btn.dataset.user, { method: 'DELETE', headers: authHeaders() });
        loadAdminUsers();
      })
    );
  } catch (e) {
    el.innerHTML = '<div class="empty-admin">Erro ao carregar usuários.</div>';
  }
}

// ---- Sugestões de código ----

async function loadAdminSuggestions() {
  const el = $('#panel-suggestions');
  el.innerHTML = '<div class="empty-admin">Carregando...</div>';
  try {
    const res = await fetch('/admin/suggestions', { headers: authHeaders() });
    const items = await res.json();
    if (!items.length) {
      el.innerHTML = '<div class="empty-admin">Nenhuma sugestão ainda. Peça pra ela sugerir algo numa conversa!</div>';
      return;
    }
    const esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    el.innerHTML = items.map((s) => `
      <div class="suggestion-card" data-id="${s.id}">
        <div class="top">
          <div>
            <div class="title">${esc(s.title)}</div>
            <div class="file">${esc(s.file_hint) || 'arquivo não especificado'} · por ${esc(s.username)}</div>
          </div>
          <span class="status-pill ${s.status}">${s.status}</span>
        </div>
        <div class="desc">${esc(s.description)}</div>
        <pre>${esc(s.code)}</pre>
        <div class="admin-actions">
          <button class="ghost-btn" data-action="copy">Copiar código</button>
          <button class="ghost-btn accent" data-action="aprovada">Marcar aprovada</button>
          <button class="ghost-btn" data-action="rejeitada">Rejeitar</button>
          <button class="danger-btn" data-action="delete">Excluir</button>
        </div>
      </div>
    `).join('');

    el.querySelectorAll('.suggestion-card').forEach((card) => {
      const id = card.dataset.id;
      card.querySelector('[data-action="copy"]').addEventListener('click', () => {
        navigator.clipboard.writeText(card.querySelector('pre').textContent);
      });
      card.querySelector('[data-action="aprovada"]').addEventListener('click', () => setSuggestionStatus(id, 'aprovada'));
      card.querySelector('[data-action="rejeitada"]').addEventListener('click', () => setSuggestionStatus(id, 'rejeitada'));
      card.querySelector('[data-action="delete"]').addEventListener('click', async () => {
        if (!confirm('Excluir esta sugestão?')) return;
        await fetch('/admin/suggestions/' + id, { method: 'DELETE', headers: authHeaders() });
        loadAdminSuggestions();
      });
    });
  } catch (e) {
    el.innerHTML = '<div class="empty-admin">Erro ao carregar sugestões.</div>';
  }
}

async function setSuggestionStatus(id, status) {
  await fetch(`/admin/suggestions/${id}/status`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
  loadAdminSuggestions();
  loadAdminStats();
}

// ---------- Inicialização ----------

(async function init() {
  await loadPublicConfig();
  if (!token) {
    showAuth();
    return;
  }
  try {
    const res = await fetch('/me', { headers: authHeaders() });
    if (!res.ok) {
      showAuth();
      return;
    }
    currentUser = await res.json();
    showApp();
  } catch (e) {
    showAuth();
  }
})();
