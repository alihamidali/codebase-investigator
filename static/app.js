/* global marked */
'use strict';

// ── State ──────────────────────────────────────────────────────────────────
let sessionId = null;
let repoName = null;
let isStreaming = false;
let turnCount = 0;
let auditHistory = [];

// Current streaming state
let currentAgentEl = null;
let streamBuffer = '';

// ── Setup ──────────────────────────────────────────────────────────────────
marked.setOptions({
  breaks: true,
  gfm: true,
});

document.getElementById('setup-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  await cloneRepo();
});

function setUrl(url) {
  document.getElementById('repo-url').value = url;
}

function newSession() {
  sessionId = null;
  repoName = null;
  isStreaming = false;
  turnCount = 0;
  auditHistory = [];
  currentAgentEl = null;
  streamBuffer = '';
  document.getElementById('messages').innerHTML = '';
  document.getElementById('audit-content').innerHTML = `
    <div class="audit-placeholder">
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 12l2 2 4-4"/><path d="M21 12c0 4.97-4.03 9-9 9S3 16.97 3 12 7.03 3 12 3s9 4.03 9 9z"/></svg>
      <p>Independent audit reports appear here after each answer.</p>
      <p class="audit-hint">Every response is checked by a separate model that verifies citations against the actual code and flags overclaims, gaps, and contradictions.</p>
    </div>`;
  document.getElementById('audit-history').classList.add('hidden');
  document.getElementById('audit-history-list').innerHTML = '';
  document.getElementById('audit-turn-badge').classList.add('hidden');
  showScreen('setup');
}

// ── Clone ──────────────────────────────────────────────────────────────────
async function cloneRepo() {
  const url = document.getElementById('repo-url').value.trim();
  if (!url) return;

  const btn = document.getElementById('clone-btn');
  const btnText = btn.querySelector('.btn-text');
  const btnSpinner = btn.querySelector('.btn-spinner');
  const errorEl = document.getElementById('clone-error');

  btn.disabled = true;
  btnText.textContent = 'Cloning...';
  btnSpinner.classList.remove('hidden');
  errorEl.classList.add('hidden');

  try {
    const res = await fetch('/api/clone', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Clone failed');

    sessionId = data.session_id;
    repoName = data.repo_name;

    document.getElementById('repo-name-display').textContent = data.repo_name;
    const repoLink = document.getElementById('repo-link');
    repoLink.href = data.repo_url;

    updateSessionStats();
    showScreen('main');

    // Show welcome message
    appendSystemMessage(`Repository **${data.repo_name}** loaded. Ask anything about the code.`);

  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btnText.textContent = 'Investigate';
    btnSpinner.classList.add('hidden');
  }
}

// ── Send Question ──────────────────────────────────────────────────────────
function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendQuestion();
  }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}

async function sendQuestion() {
  if (isStreaming || !sessionId) return;

  const input = document.getElementById('question-input');
  const question = input.value.trim();
  if (!question) return;

  input.value = '';
  input.style.height = 'auto';

  isStreaming = true;
  document.getElementById('send-btn').disabled = true;

  // Show user message
  appendMessage('user', question);

  // Create agent message placeholder
  currentAgentEl = createAgentPlaceholder();
  streamBuffer = '';

  // Show tool activity
  showToolActivity('Starting investigation...');

  try {
    await streamResponse(question);
  } catch (err) {
    finalizeAgentMessage(`*Error: ${err.message}*`);
  } finally {
    isStreaming = false;
    document.getElementById('send-btn').disabled = false;
    hideToolActivity();
    currentAgentEl = null;
    streamBuffer = '';
  }
}

async function streamResponse(question) {
  const res = await fetch(`/api/ask/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let currentEvent = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        const raw = line.slice(6);
        let data;
        try { data = JSON.parse(raw); } catch { continue; }
        handleEvent(currentEvent, data);
      }
    }
  }
}

// ── Event Handling ─────────────────────────────────────────────────────────
function handleEvent(type, data) {
  switch (type) {
    case 'tool_start':
      showToolActivity(formatToolStart(data.name));
      break;

    case 'tool_execute':
      showToolActivity(formatToolExecute(data.name, data.input));
      break;

    case 'tool_done':
      // Brief visual feedback — keep the activity bar showing
      break;

    case 'token':
      appendToken(data.text || '');
      break;

    case 'answer_complete':
      finalizeAgentMessage(data.text || streamBuffer);
      hideToolActivity();
      break;

    case 'audit':
      renderAudit(data);
      turnCount++;
      updateSessionStats();
      break;

    case 'error':
      if (currentAgentEl && !streamBuffer) {
        finalizeAgentMessage(`*Investigation error: ${data.message}*`);
      }
      break;

    case 'complete':
      // Nothing needed — already finalized
      break;
  }
}

function formatToolStart(name) {
  const icons = {
    read_file: '📄',
    search_code: '🔍',
    list_files: '📂',
    get_structure: '🗂️',
  };
  const labels = {
    read_file: 'Reading file',
    search_code: 'Searching code',
    list_files: 'Listing files',
    get_structure: 'Getting structure',
  };
  return `${icons[name] || '⚙️'} ${labels[name] || name}...`;
}

function formatToolExecute(name, input) {
  if (name === 'read_file') {
    const loc = input.start_line
      ? `${input.path}:${input.start_line}${input.end_line ? '-' + input.end_line : ''}`
      : input.path;
    return `📄 Reading ${loc}`;
  }
  if (name === 'search_code') {
    const scope = input.file_glob ? ` in ${input.file_glob}` : '';
    return `🔍 Searching for "${input.pattern}"${scope}`;
  }
  if (name === 'list_files') {
    return `📂 Listing ${input.directory || 'root'}${input.pattern ? ' (' + input.pattern + ')' : ''}`;
  }
  if (name === 'get_structure') {
    return `🗂️ Getting structure (depth ${input.max_depth || 3})`;
  }
  return `⚙️ ${name}`;
}

// ── DOM: Messages ──────────────────────────────────────────────────────────
function appendMessage(role, text) {
  const el = document.createElement('div');
  el.className = `message ${role}`;
  el.innerHTML = `
    <div class="message-role">${role === 'user' ? 'You' : 'Investigator'}</div>
    <div class="message-body">${role === 'user' ? escHtml(text) : marked.parse(text)}</div>
  `;
  document.getElementById('messages').appendChild(el);
  scrollToBottom();
  return el;
}

function appendSystemMessage(md) {
  const el = document.createElement('div');
  el.className = 'message agent';
  el.innerHTML = `
    <div class="message-role">System</div>
    <div class="message-body">${marked.parse(md)}</div>
  `;
  document.getElementById('messages').appendChild(el);
}

function createAgentPlaceholder() {
  const el = document.createElement('div');
  el.className = 'message agent';
  el.innerHTML = `
    <div class="message-role">Investigator</div>
    <div class="message-body" id="stream-target"><span class="streaming-cursor"></span></div>
  `;
  document.getElementById('messages').appendChild(el);
  scrollToBottom();
  return el;
}

function appendToken(text) {
  streamBuffer += text;
  const target = document.getElementById('stream-target');
  if (!target) return;
  // Show raw text while streaming — render markdown at the end
  target.innerHTML = escHtml(streamBuffer) + '<span class="streaming-cursor"></span>';
  scrollToBottom();
}

function finalizeAgentMessage(text) {
  const target = document.getElementById('stream-target');
  if (!target) return;

  const rendered = marked.parse(text || streamBuffer || '*No response*');
  target.innerHTML = rendered;
  target.id = '';

  // Make citations clickable
  makeCitationsClickable(target);
  scrollToBottom();
}

function makeCitationsClickable(container) {
  // Find all <code> elements that look like citations
  container.querySelectorAll('code').forEach((code) => {
    const text = code.textContent.trim();
    const match = text.match(/^([\w/.-]+\.[\w]+):(\d+)(?:-(\d+))?$/);
    if (match) {
      const path = match[1];
      const start = parseInt(match[2]);
      const end = match[3] ? parseInt(match[3]) : null;
      const chip = document.createElement('span');
      chip.className = 'citation-chip';
      chip.textContent = text;
      chip.title = `Click to view ${path}`;
      chip.onclick = () => showCitation(path, start, end);
      code.replaceWith(chip);
    }
  });
}

// ── Tool Activity Bar ──────────────────────────────────────────────────────
function showToolActivity(msg) {
  document.getElementById('tool-activity').classList.remove('hidden');
  document.getElementById('tool-activity-text').textContent = msg;
}

function hideToolActivity() {
  document.getElementById('tool-activity').classList.add('hidden');
}

// ── Audit Panel ────────────────────────────────────────────────────────────
function renderAudit(audit) {
  const turn = auditHistory.length + 1;
  auditHistory.push({ turn, audit });

  const badge = document.getElementById('audit-turn-badge');
  badge.textContent = `Turn ${turn}`;
  badge.classList.remove('hidden');

  const content = document.getElementById('audit-content');
  content.innerHTML = buildAuditCard(audit, turn);

  // Make citation paths in audit clickable
  content.querySelectorAll('.cite-path').forEach((el) => {
    const citation = el.dataset.citation;
    if (citation) {
      const m = citation.match(/^([\w/.-]+\.[\w]+):(\d+)-(\d+)$/);
      if (m) {
        el.onclick = () => showCitation(m[1], parseInt(m[2]), parseInt(m[3]));
      }
    }
  });

  updateAuditHistory();
}

function buildAuditCard(audit, turn) {
  const trust = audit.trust_level || 'UNKNOWN';
  const trustLabels = {
    HIGH: '✓ HIGH TRUST', MEDIUM: '◐ MEDIUM TRUST',
    LOW: '⚠ LOW TRUST', UNRELIABLE: '✗ UNRELIABLE', UNKNOWN: '? UNKNOWN',
  };

  let html = `<div class="audit-card">`;

  // Trust banner
  html += `<div class="trust-banner trust-${trust}">${trustLabels[trust] || trust}</div>`;

  // Summary
  if (audit.summary) {
    html += `<div class="audit-section"><div class="audit-section-title">Summary</div><div class="audit-summary">${escHtml(audit.summary)}</div></div>`;
  }

  // Citation verification
  const verifications = audit.citation_verification || [];
  const issues = audit.citation_issues || [];

  if (verifications.length > 0) {
    html += `<div class="audit-section"><div class="audit-section-title">Citations (${verifications.length})</div>`;
    for (const v of verifications) {
      const ok = v.exists;
      const issueEntry = issues.find((i) => i.citation === v.citation);
      const hasIssue = !ok || issueEntry;

      html += `<div class="citation-row ${ok && !issueEntry ? 'ok' : 'fail'}">
        <span class="cite-icon">${ok && !issueEntry ? '✓' : '✗'}</span>
        <div>
          <span class="cite-path" data-citation="${escAttr(v.citation)}" style="cursor:pointer">${escHtml(v.citation)}</span>
          ${!ok ? `<div class="cite-issue">${escHtml(v.issue || 'File not found')}</div>` : ''}
          ${issueEntry ? `<div class="cite-issue">${escHtml(issueEntry.issue)}</div>` : ''}
        </div>
      </div>`;
    }
    html += `</div>`;
  }

  // Reasoning issues
  const reasoningIssues = audit.reasoning_issues || [];
  const significant = reasoningIssues.filter((i) => i.type !== 'AUDIT_ERROR');
  if (significant.length > 0) {
    html += `<div class="audit-section"><div class="audit-section-title">Issues (${significant.length})</div>`;
    for (const issue of significant) {
      const sev = issue.severity || 'LOW';
      html += `<div class="issue-row issue-${sev}">
        <div class="issue-type">${escHtml(issue.type)} · ${sev}</div>
        <div class="issue-desc">${escHtml(issue.description)}</div>
      </div>`;
    }
    html += `</div>`;
  }

  // Contradictions
  const contradictions = audit.contradictions_with_prior || [];
  if (contradictions.length > 0) {
    html += `<div class="audit-section"><div class="audit-section-title">Contradictions (${contradictions.length})</div>`;
    for (const c of contradictions) {
      html += `<div class="issue-row issue-HIGH">
        <div class="issue-type">CONTRADICTION · Turn ${c.turn || '?'}</div>
        <div class="issue-desc">Prior: ${escHtml(c.prior_claim)}<br>Now: ${escHtml(c.current_claim)}</div>
      </div>`;
    }
    html += `</div>`;
  }

  // New claims logged
  const newClaims = audit.new_claims || [];
  if (newClaims.length > 0) {
    html += `<div class="audit-section"><div class="audit-section-title">New Claims Logged (${newClaims.length})</div><div class="claims-list">`;
    for (const c of newClaims) {
      const conf = c.confidence || 'low';
      html += `<div class="claim-item ${conf}">
        <span class="claim-dot">+</span>
        <div>
          <span class="claim-text">${escHtml(c.claim)}</span>
          ${c.citation ? ` <span class="claim-cite">${escHtml(c.citation)}</span>` : ''}
        </div>
      </div>`;
    }
    html += `</div></div>`;
  }

  html += `</div>`;
  return html;
}

function updateAuditHistory() {
  if (auditHistory.length < 2) return;

  const historyEl = document.getElementById('audit-history');
  const listEl = document.getElementById('audit-history-list');
  historyEl.classList.remove('hidden');

  listEl.innerHTML = '<div class="history-items">' +
    auditHistory.map(({ turn, audit }) => {
      const trust = audit.trust_level || 'UNKNOWN';
      const dots = { HIGH: '●', MEDIUM: '◐', LOW: '◑', UNRELIABLE: '○', UNKNOWN: '?' };
      return `<span class="history-dot ${trust}" onclick="showHistoricalAudit(${turn - 1})" title="Turn ${turn}: ${trust}">${dots[trust] || '?'} T${turn}</span>`;
    }).join('') +
    '</div>';
}

function showHistoricalAudit(index) {
  const { turn, audit } = auditHistory[index];
  const badge = document.getElementById('audit-turn-badge');
  badge.textContent = `Turn ${turn}`;
  badge.classList.remove('hidden');

  const content = document.getElementById('audit-content');
  content.innerHTML = buildAuditCard(audit, turn);

  content.querySelectorAll('.cite-path').forEach((el) => {
    const citation = el.dataset.citation;
    if (citation) {
      const m = citation.match(/^([\w/.-]+\.[\w]+):(\d+)-(\d+)$/);
      if (m) el.onclick = () => showCitation(m[1], parseInt(m[2]), parseInt(m[3]));
    }
  });
}

// ── Citation Modal ─────────────────────────────────────────────────────────
async function showCitation(path, start, end) {
  const endParam = end ? `&end=${end}` : '';
  const url = `/api/file/${sessionId}?path=${encodeURIComponent(path)}&start=${start}${endParam}`;

  try {
    const res = await fetch(url);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || 'File not found');
    }
    const data = await res.json();

    document.getElementById('modal-title').textContent = `${path}  (lines ${data.shown}, ${data.total_lines} total)`;
    document.getElementById('modal-code').textContent = data.content;
    document.getElementById('citation-modal').classList.remove('hidden');
  } catch (err) {
    alert(`Could not load ${path}: ${err.message}`);
  }
}

function closeModal(e) {
  if (e.target === document.getElementById('citation-modal')) {
    closeCitationModal();
  }
}

function closeCitationModal() {
  document.getElementById('citation-modal').classList.add('hidden');
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeCitationModal();
});

// ── Utilities ──────────────────────────────────────────────────────────────
function showScreen(name) {
  document.querySelectorAll('.screen').forEach((s) => {
    s.classList.remove('active');
    s.classList.add('hidden');
  });
  const target = name === 'setup' ? 'setup-screen' : 'main-screen';
  const el = document.getElementById(target);
  el.classList.remove('hidden');
  el.classList.add('active');

  if (name === 'main') {
    setTimeout(() => document.getElementById('question-input').focus(), 100);
  }
}

function scrollToBottom() {
  const msgs = document.getElementById('messages');
  msgs.scrollTop = msgs.scrollHeight;
}

function updateSessionStats() {
  const el = document.getElementById('session-stats');
  const claimsText = auditHistory.length > 0
    ? ` · ${auditHistory.reduce((n, { audit }) => n + (audit.new_claims || []).length, 0)} claims`
    : '';
  el.textContent = `${turnCount} turn${turnCount !== 1 ? 's' : ''}${claimsText}`;
}

function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escAttr(str) {
  return escHtml(str);
}
