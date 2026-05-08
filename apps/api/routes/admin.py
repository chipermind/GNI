"""Minimal admin UI: pause, review, DLQ. No auth on page load; API calls use X-API-Key from sessionStorage."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["admin"])

ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GNI Admin</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 800px; margin: 1rem auto; padding: 0 1rem; }
    h1 { font-size: 1.25rem; }
    .section { margin: 1.5rem 0; }
    .key-form { margin-bottom: 1rem; }
    input[type="password"] { width: 280px; padding: 4px 8px; }
    button { padding: 6px 12px; margin: 2px; cursor: pointer; }
    .paused { background: #fcc; }
    .ok { background: #cfc; }
    ul { list-style: none; padding: 0; }
    li { padding: 8px; margin: 4px 0; border: 1px solid #ccc; border-radius: 4px; }
    .error { color: #c00; }
    .meta { font-size: 0.85em; color: #666; }
  </style>
</head>
<body>
  <h1>GNI Bot Admin</h1>
  <div class="key-form">
    <label>API Key: </label>
    <input type="password" id="apiKey" placeholder="X-API-Key (required if auth enabled)">
    <button onclick="saveKey()">Save</button>
    <span id="keyStatus"></span>
  </div>

  <div class="section">
    <h2>Publish</h2>
    <p>Status: <span id="pauseStatus">—</span></p>
    <button id="btnPause" onclick="pause()">Pause</button>
    <button id="btnResume" onclick="resume()">Resume</button>
    <button onclick="refresh()">Refresh</button>
  </div>

  <div class="section">
    <h2>Pending Review</h2>
    <ul id="pending"></ul>
  </div>

  <div class="section">
    <h2>Dead Letter Queue</h2>
    <ul id="dlq"></ul>
  </div>

  <script>
    function headers() {
      const k = sessionStorage.getItem('gni_api_key') || '';
      return k ? { 'X-API-Key': k } : {};
    }
    function saveKey() {
      const v = document.getElementById('apiKey').value.trim();
      if (v) { sessionStorage.setItem('gni_api_key', v); document.getElementById('keyStatus').textContent = 'Saved'; }
    }
    async function api(path, opts = {}) {
      const r = await fetch(path, { ...opts, headers: { 'Content-Type': 'application/json', ...headers(), ...(opts.headers || {}) } });
      if (r.status === 401) { document.getElementById('keyStatus').textContent = 'Unauthorized — set API key'; throw new Error('Unauthorized'); }
      return r.json ? r.json() : r;
    }
    const esc = s => (''+s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    async function refresh() {
      try {
        const st = await api('/control/status');
        document.getElementById('pauseStatus').textContent = st.settings?.pause_all_publish ? 'PAUSED' : 'Running';
        document.getElementById('pauseStatus').className = st.settings?.pause_all_publish ? 'paused' : 'ok';
      } catch (e) { document.getElementById('pauseStatus').textContent = 'Error: ' + e.message; }
      try {
        const pending = await api('/review/pending');
        const ul = document.getElementById('pending');
        ul.innerHTML = pending.length ? pending.map(p => `<li><strong>${esc(p.title || p.id)}</strong><br><span class="meta">${esc(p.source_name||'')} #${p.id}</span><br><button onclick="approve(${p.id})">Approve</button><button onclick="reject(${p.id})">Reject</button></li>`).join('') : '<li>None</li>';
      } catch (e) { document.getElementById('pending').innerHTML = '<li class="error">' + e.message + '</li>'; }
      try {
        const dlq = await api('/dlq');
        const ul = document.getElementById('dlq');
        ul.innerHTML = dlq.length ? dlq.map(d => `<li><strong>#${d.item_id}</strong> ${esc(d.stage)}: ${esc((d.error||'').slice(0,80))}<br><span class="meta">${d.last_seen} attempts=${d.attempts}</span><br><button onclick="retry(${d.id})">Retry</button><button onclick="drop(${d.id})">Drop</button></li>`).join('') : '<li>None</li>';
      } catch (e) { document.getElementById('dlq').innerHTML = '<li class="error">' + e.message + '</li>'; }
    }
    async function pause() { await api('/control/pause', { method: 'POST' }); refresh(); }
    async function resume() { await api('/control/resume', { method: 'POST' }); refresh(); }
    async function approve(id) { await api('/review/' + id + '/approve', { method: 'POST' }); refresh(); }
    async function reject(id) { await api('/review/' + id + '/reject', { method: 'POST' }); refresh(); }
    async function retry(id) { await api('/dlq/' + id + '/retry', { method: 'POST' }); refresh(); }
    async function drop(id) { await api('/dlq/' + id + '/drop', { method: 'POST' }); refresh(); }
    if (sessionStorage.getItem('gni_api_key')) document.getElementById('apiKey').placeholder = '(saved)';
    refresh();
  </script>
</body>
</html>
"""


@router.get("/admin", response_class=HTMLResponse)
def admin_ui():
    """Minimal admin UI: pause flag, pending review (approve/reject), DLQ (retry/drop)."""
    return ADMIN_HTML
