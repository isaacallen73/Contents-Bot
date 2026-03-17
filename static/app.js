/* ═══════════════════════════════════════════════════════════════════════
   Liberty Restoration — Inventory Bot
   ═══════════════════════════════════════════════════════════════════════ */

// ── State ────────────────────────────────────────────────────────────────────
let currentSessionId = null;
let sessionData      = null;
let workingGroups    = [];   // [[filename, ...], ...] on Group screen
let allItems         = [];
let filterMode       = 'all';
let currentPage      = 1;
const PAGE_SIZE      = 100;
let priceSearchEnabled = false;
let sseSource        = null;
const priceSearchContext = {};  // itemId -> messages array for multi-turn Q&A

// ── Utilities ────────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).error || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  const el = document.getElementById('screen-' + id);
  if (el) el.classList.add('active');

  const headerScreens = ['import', 'group', 'review', 'settings'];
  const stepScreens   = ['import', 'group', 'review'];

  document.getElementById('app-header').classList.toggle('hidden', !headerScreens.includes(id));
  document.getElementById('step-indicator').classList.toggle('hidden', !stepScreens.includes(id));

  if (stepScreens.includes(id)) {
    const steps = { import: 1, group: 2, review: 3 };
    const active = steps[id];
    [1, 2, 3].forEach(n => {
      const el = document.getElementById('step-' + n);
      el.classList.remove('active', 'done');
      if (n === active) el.classList.add('active');
      if (n < active)  el.classList.add('done');
    });
  }

  window.scrollTo(0, 0);
}

function showToast(msg, type = 'info', duration = 3000) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + type;
  t.classList.remove('hidden');
  setTimeout(() => t.classList.add('hidden'), duration);
}

function formatCurrency(val) {
  if (val == null || val === '') return '—';
  const n = parseFloat(val);
  if (isNaN(n)) return '—';
  return '$' + n.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function formatTotal(price, qty) {
  if (price == null || price === '') return '—';
  const p = parseFloat(price), q = parseInt(qty) || 1;
  if (isNaN(p)) return '—';
  return formatCurrency(p * q);
}

function escHtml(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function thumbUrl(sid, filename) {
  return `/api/sessions/${sid}/thumb/${encodeURIComponent(filename)}`;
}
function photoUrl(sid, filename) {
  return `/api/sessions/${sid}/photo/${encodeURIComponent(filename)}`;
}

// ── Init ─────────────────────────────────────────────────────────────────────
function populateModelSelect(selectId, models, current) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  sel.innerHTML = Object.entries(models).map(([id, label]) =>
    `<option value="${escHtml(id)}" ${id === current ? 'selected' : ''}>${escHtml(label)}</option>`
  ).join('');
}

async function init() {
  try {
    const cfg = await api('GET', '/api/config');
    priceSearchEnabled = cfg.has_anthropic_key;
    populateModelSelect('settings-model', cfg.available_models || {}, cfg.model);
    if (!cfg.has_anthropic_key) {
      showScreen('setup');
    } else {
      showScreen('home');
      loadHome();
    }
  } catch (e) {
    showScreen('setup');
  }
}

// ── Setup ─────────────────────────────────────────────────────────────────────
async function saveSetup() {
  const key = document.getElementById('setup-anthropic-key').value.trim();
  const err = document.getElementById('setup-error');

  if (!key) {
    err.textContent = 'Please enter your Anthropic API key.';
    err.classList.remove('hidden');
    return;
  }
  err.classList.add('hidden');

  try {
    await api('POST', '/api/config', { anthropic_api_key: key });
    priceSearchEnabled = true;
    showScreen('home');
    loadHome();
  } catch (e) {
    err.textContent = e.message;
    err.classList.remove('hidden');
  }
}

// ── Settings ──────────────────────────────────────────────────────────────────
function showSettings() {
  showScreen('settings');
  document.getElementById('app-header').classList.remove('hidden');
  document.getElementById('step-indicator').classList.add('hidden');
}

async function saveSettings() {
  const key   = document.getElementById('settings-anthropic-key').value.trim();
  const model = document.getElementById('settings-model').value;
  const msg   = document.getElementById('settings-msg');

  const payload = {};
  if (key)   payload.anthropic_api_key = key;
  if (model) payload.model             = model;

  try {
    await api('POST', '/api/config', payload);
    priceSearchEnabled = true;
    msg.textContent = 'Saved!';
    msg.className = '';
    msg.style.color = 'var(--success-color)';
    msg.classList.remove('hidden');
    setTimeout(() => { msg.classList.add('hidden'); goHome(); }, 1200);
  } catch (e) {
    msg.textContent = e.message;
    msg.style.color = 'var(--red)';
    msg.classList.remove('hidden');
  }
}

// ── Home ──────────────────────────────────────────────────────────────────────
async function loadHome() {
  try {
    const sessions = await api('GET', '/api/sessions');
    const list = document.getElementById('home-sessions-list');
    const wrap = document.getElementById('home-sessions-wrap');

    if (!sessions.length) {
      wrap.classList.add('hidden');
      return;
    }
    wrap.classList.remove('hidden');
    list.innerHTML = sessions.map(s => {
      const date = s.created_at ? new Date(s.created_at).toLocaleDateString() : '';
      const statusClass = 'status-' + s.status;
      const statusLabel = { grouping: 'Grouping', processing: 'Processing', reviewing: 'In Review', exported: 'Exported' }[s.status] || s.status;
      return `
        <div class="session-card" onclick="resumeSession('${escHtml(s.session_id)}')">
          <div>
            <div class="session-folder">${escHtml(s.folder_name || s.folder_path)}</div>
            <div class="session-meta">${escHtml(date)} &nbsp;&bull;&nbsp; ${s.photo_count} photos &nbsp;&bull;&nbsp; ${s.item_count} items</div>
          </div>
          <span class="session-status ${statusClass}">${statusLabel}</span>
        </div>`;
    }).join('');
  } catch (e) {
    // Sessions list is optional — fail silently
  }
}

function goHome() {
  if (sseSource) { sseSource.close(); sseSource = null; }
  showScreen('home');
  loadHome();
}

function startNew() {
  currentSessionId = null;
  sessionData      = null;
  workingGroups    = [];
  allItems         = [];
  document.getElementById('folder-label').textContent = 'Click to browse for folder';
  document.getElementById('folder-count').classList.add('hidden');
  document.getElementById('import-thumbs-wrap').classList.add('hidden');
  document.getElementById('btn-import-continue').disabled = true;
  document.getElementById('import-error').classList.add('hidden');
  showScreen('import');
}

async function resumeSession(id) {
  try {
    const data = await api('GET', `/api/sessions/${id}`);
    currentSessionId = data.session_id;
    sessionData      = data;
    allItems         = data.items || [];

    const statusScreens = {
      grouping:   'group',
      processing: 'process',
      reviewing:  'review',
      exported:   'review',
    };
    const target = statusScreens[data.status] || 'review';

    if (target === 'group') {
      workingGroups = data.groups || data.photos.map(p => [p]);
      renderFilmstrip();
      showScreen('group');
    } else if (target === 'process') {
      // Attempt to resume live processing on the review screen
      workingGroups = data.groups || [];
      allItems = data.items || [];
      showScreen('review');
      document.getElementById('btn-search-all').style.display = priceSearchEnabled ? '' : 'none';
      currentPage = 1;
      renderReviewTable();
      const groupCount = workingGroups.length;
      _showProcessingBanner(allItems.length, groupCount);
      connectProgressSSE(); // gracefully hides banner if no active job
    } else {
      showReviewScreen();
    }
  } catch (e) {
    showToast('Could not load session: ' + e.message, 'error');
  }
}

// ── Import ────────────────────────────────────────────────────────────────────
async function browseFolder() {
  try {
    const res = await api('POST', '/api/browse-folder');
    if (!res.path) return;
    document.getElementById('folder-label').textContent = res.path;
    document.getElementById('folder-count').classList.add('hidden');
    document.getElementById('import-error').classList.add('hidden');
    document.getElementById('btn-import-continue').disabled = true;

    // Create session to get photo list
    const session = await api('POST', '/api/sessions', { folder_path: res.path });
    currentSessionId = session.session_id;
    sessionData      = session;

    document.getElementById('folder-count').textContent = `${session.photos.length} photos found`;
    document.getElementById('folder-count').classList.remove('hidden');
    document.getElementById('btn-import-continue').disabled = false;

    // Preview thumbnails (first 20)
    const preview = session.photos.slice(0, 20);
    const wrap = document.getElementById('import-thumbs-wrap');
    const strip = document.getElementById('import-thumbs');
    strip.innerHTML = preview.map(p =>
      `<img src="${thumbUrl(session.session_id, p)}" loading="lazy" alt="${escHtml(p)}">`
    ).join('');
    if (session.photos.length > 20) {
      strip.innerHTML += `<div style="display:flex;align-items:center;padding:0 12px;color:var(--mid-gray);font-size:12px;white-space:nowrap">+${session.photos.length - 20} more</div>`;
    }
    wrap.classList.remove('hidden');

  } catch (e) {
    const errEl = document.getElementById('import-error');
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
}

function confirmImport() {
  if (!currentSessionId || !sessionData) return;
  workingGroups = sessionData.groups || sessionData.photos.map(p => [p]);
  renderFilmstrip();
  showScreen('group');
}

// ── Group ─────────────────────────────────────────────────────────────────────
function renderFilmstrip() {
  const container = document.getElementById('filmstrip');
  const totalPhotos = workingGroups.reduce((s, g) => s + g.length, 0);

  document.getElementById('group-counter').textContent = `${workingGroups.length} item${workingGroups.length !== 1 ? 's' : ''}`;
  document.getElementById('group-counter-sub').textContent = `${totalPhotos} photo${totalPhotos !== 1 ? 's' : ''}`;
  const cost = (totalPhotos * 0.003).toFixed(2);
  const costEl = document.getElementById('group-cost-estimate');
  if (costEl) costEl.innerHTML = `Estimated processing cost: <strong>~$${cost}</strong>`;

  let html = '';
  workingGroups.forEach((group, gIdx) => {
    const isMulti = group.length > 1;
    html += `<div class="film-group${isMulti ? ' multi' : ''}">`;
    html += `<div class="film-photos">`;
    group.forEach((photo, pIdx) => {
      html += `<div class="film-thumb">`;
      html += `<img src="${thumbUrl(currentSessionId, photo)}" loading="lazy" alt="${escHtml(photo)}">`;
      if (isMulti && pIdx > 0) {
        html += `<button class="film-split-btn" onclick="splitGroup(${gIdx},${pIdx})" title="Split here">✂</button>`;
      }
      html += `<span class="film-thumb-name">${escHtml(photo)}</span>`;
      html += `</div>`;
    });
    html += `</div>`;
    if (isMulti) {
      html += `<div class="film-group-badge">${group.length} photos · 1 item</div>`;
    }
    html += `</div>`;

    // Divider between groups
    if (gIdx < workingGroups.length - 1) {
      html += `
        <div class="film-divider" onclick="mergeAtDivider(${gIdx})" title="Merge into one item">
          <div class="film-divider-line"></div>
          <button class="film-merge-btn">&#8853;</button>
          <div class="film-divider-line"></div>
        </div>`;
    }
  });

  container.innerHTML = html;
}

function mergeAtDivider(idx) {
  if (idx < 0 || idx >= workingGroups.length - 1) return;
  const merged = [...workingGroups[idx], ...workingGroups[idx + 1]];
  workingGroups.splice(idx, 2, merged);
  renderFilmstrip();
}

function splitGroup(groupIdx, photoIdx) {
  if (photoIdx <= 0) return;
  const group = workingGroups[groupIdx];
  workingGroups.splice(groupIdx, 1, group.slice(0, photoIdx), group.slice(photoIdx));
  renderFilmstrip();
}

async function confirmGroups() {
  const btn = document.getElementById('btn-start-processing');
  if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }

  try {
    await api('POST', `/api/sessions/${currentSessionId}/groups`, { groups: workingGroups });
    sessionData.groups = workingGroups;

    allItems = [];
    showScreen('review');
    document.getElementById('btn-search-all').style.display = priceSearchEnabled ? '' : 'none';
    currentPage = 1;
    renderReviewTable();
    _showProcessingBanner(0, workingGroups.length);

    await api('POST', `/api/sessions/${currentSessionId}/process`);
    connectProgressSSE();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Start Processing →'; }
  }
}

// ── Process (removed — confirmGroups() now handles this flow) ─────────────────
/*
function showProcessScreen() {
  showScreen('process');
  const count = workingGroups.length || (sessionData && sessionData.groups && sessionData.groups.length) || 0;
  const photos = (workingGroups.length ? workingGroups : (sessionData && sessionData.groups) || [])
    .reduce((s, g) => s + g.length, 0);
  const cost = (photos * 0.003).toFixed(2);
  document.getElementById('process-estimate').textContent =
    `${count} items · ${photos} photos · Estimated cost: ~$${cost}`;

  document.getElementById('process-ready').classList.remove('hidden');
  document.getElementById('process-running').classList.add('hidden');
  document.getElementById('process-done').classList.add('hidden');
  document.getElementById('process-error').classList.add('hidden');
}

async function startProcessing() {
  const btn = document.querySelector('#process-ready .btn-primary');
  if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }

  try {
    await api('POST', `/api/sessions/${currentSessionId}/process`);
    // Navigate to review immediately — items will stream in as they complete
    allItems = [];
    showScreen('review');
    document.getElementById('btn-search-all').style.display = priceSearchEnabled ? '' : 'none';
    currentPage = 1;
    renderReviewTable();
    const groupCount = (workingGroups.length || (sessionData && sessionData.groups && sessionData.groups.length) || 0);
    _showProcessingBanner(0, groupCount);
    connectProgressSSE();
  } catch (e) {
    document.getElementById('process-error').textContent = e.message;
    document.getElementById('process-error').classList.remove('hidden');
    if (btn) { btn.disabled = false; btn.textContent = 'Start Processing'; }
  }
}
*/

function connectProgressSSE() {
  if (sseSource) sseSource.close();
  sseSource = new EventSource(`/api/sessions/${currentSessionId}/progress`);

  sseSource.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.ping) return;

    if (data.error) {
      sseSource.close();
      _hideProcessingBanner();
      if (data.error !== 'No active job') {
        showToast('Processing error: ' + data.error, 'error');
      }
      return;
    }

    if (data.completed !== undefined && data.total) {
      _updateProcessingBanner(data.completed, data.total);

      if (data.new_items && data.new_items.length) {
        for (const item of data.new_items) {
          const idx = allItems.findIndex(i => i.id === item.id);
          if (idx >= 0) allItems[idx] = item;
          else allItems.push(item);
        }
        renderReviewTable();
      }
    }

    if (data.done) {
      sseSource.close();
      api('GET', `/api/sessions/${currentSessionId}`).then(d => {
        sessionData = d;
        allItems = d.items || [];
        _hideProcessingBanner();
        renderReviewTable();
        showToast('Processing complete!', 'success');
      });
    }
  };

  sseSource.onerror = () => {
    sseSource.close();
    api('GET', `/api/sessions/${currentSessionId}`).then(d => {
      if (d.status === 'reviewing') {
        sessionData = d;
        allItems = d.items || [];
        _hideProcessingBanner();
        renderReviewTable();
      }
    }).catch(() => {});
  };
}

function _showProcessingBanner(completed, total) {
  const banner = document.getElementById('processing-banner');
  if (!banner) return;
  banner.classList.remove('hidden');
  _updateProcessingBanner(completed, total);
}

function _updateProcessingBanner(completed, total) {
  const text = document.getElementById('processing-banner-text');
  const bar  = document.getElementById('processing-mini-bar');
  if (text) text.textContent = `Processing ${completed} of ${total} items — results appear below as they complete.`;
  if (bar)  bar.style.width = (total > 0 ? Math.round((completed / total) * 100) : 0) + '%';
}

function _hideProcessingBanner() {
  const banner = document.getElementById('processing-banner');
  if (banner) banner.classList.add('hidden');
}

function goToReview() {
  showReviewScreen();
}

// ── Review ────────────────────────────────────────────────────────────────────
async function showReviewScreen() {
  showScreen('review');

  // Fetch fresh data if needed
  if (!allItems.length && currentSessionId) {
    try {
      const d = await api('GET', `/api/sessions/${currentSessionId}`);
      sessionData = d;
      allItems = d.items || [];
    } catch (e) {
      showToast('Could not load items: ' + e.message, 'error');
      return;
    }
  }

  // Show/hide price search button
  document.getElementById('btn-search-all').style.display = priceSearchEnabled ? '' : 'none';

  currentPage = 1;
  renderReviewTable();
}

function getFilteredItems() {
  return allItems.filter(item => {
    if (filterMode === 'attention') {
      const conf = item.confidence || {};
      return (conf.overall || 0) < 0.80 || !item.category || !item.item;
    }
    if (filterMode === 'missing') {
      return (item.price == null || item.price === '') ||
             (item.age  == null || item.age  === '');
    }
    return true;
  });
}

function setFilter(mode) {
  filterMode = mode;
  currentPage = 1;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('filter-' + mode).classList.add('active');
  renderReviewTable();
}

function renderReviewTable() {
  const filtered = getFilteredItems();
  const total    = filtered.length;
  const pages    = Math.max(1, Math.ceil(total / PAGE_SIZE));
  currentPage    = Math.min(currentPage, pages);
  const start    = (currentPage - 1) * PAGE_SIZE;
  const pageItems = filtered.slice(start, start + PAGE_SIZE);

  // Stats
  const needsAttention = allItems.filter(i => {
    const c = i.confidence || {};
    return (c.overall || 0) < 0.80 || !i.category || !i.item;
  }).length;
  const missingPriceAge = allItems.filter(i =>
    (i.price == null || i.price === '') || (i.age == null || i.age === '')
  ).length;

  document.getElementById('review-stats').innerHTML =
    `<strong>${allItems.length}</strong> items &nbsp;|&nbsp; ` +
    `<strong style="color:var(--warning-color)">${needsAttention}</strong> need attention &nbsp;|&nbsp; ` +
    `<strong style="color:#1565c0">${missingPriceAge}</strong> missing price/age`;

  // Pagination
  const paginationHtml = total > PAGE_SIZE ? `
    <button class="page-btn" onclick="changePage(${currentPage - 1})" ${currentPage <= 1 ? 'disabled' : ''}>&larr; Prev</button>
    Page ${currentPage} of ${pages} &nbsp;(${total} items)
    <button class="page-btn" onclick="changePage(${currentPage + 1})" ${currentPage >= pages ? 'disabled' : ''}>Next &rarr;</button>
  ` : `${total} item${total !== 1 ? 's' : ''}`;
  document.getElementById('pagination-bar').innerHTML = paginationHtml;
  document.getElementById('pagination-bar-bottom').innerHTML = paginationHtml;

  // Table rows
  const tbody = document.getElementById('review-tbody');
  tbody.innerHTML = pageItems.map(item => buildRow(item)).join('');
}

function changePage(page) {
  currentPage = page;
  renderReviewTable();
  document.querySelector('.table-wrap').scrollIntoView({ behavior: 'smooth' });
}

function buildRow(item) {
  const conf = item.confidence || {};
  const overall = conf.overall || 0;
  let rowClass = '';
  if (overall < 0.55 || !item.item) rowClass = 'row-danger';
  else if (overall < 0.80)          rowClass = 'row-warning';

  const flags = (conf.flags || []).filter(f => f).slice(0, 1);
  const flagHtml = flags.length ? `<span class="conf-flag" title="${escHtml(flags[0])}">&#9888; ${escHtml(flags[0])}</span>` : '';

  const pct = Math.round(overall * 100);
  const confBadgeClass = overall >= 0.80 ? 'conf-badge-good' : overall >= 0.55 ? 'conf-badge-warn' : 'conf-badge-bad';
  const flagTitle = (conf.flags || []).filter(f => f).join(' · ') || '';
  const confBadge = `<span class="conf-badge ${confBadgeClass}" title="${escHtml(flagTitle || pct + '% confidence')}">${pct}%</span>`;

  // Price suggestions dropdown (if any)
  const suggestions = item.price_suggestions || [];
  const confLabel = { high: '✓ High', medium: '~ Medium', low: '? Low' };
  const suggestHtml = suggestions.length ? `
    <div class="price-suggestions" id="sugg-${escHtml(item.id)}">
      ${suggestions.map(s => {
        const conf = s.confidence || 'medium';
        const confClass = 'sugg-conf-' + conf;
        const safeUrl = escHtml(s.url || '');
        return `
        <div class="price-suggestion-item">
          <div class="suggestion-top">
            <span class="suggestion-price">${formatCurrency(s.price)}</span>
            <span class="sugg-conf ${confClass}">${confLabel[conf] || conf}</span>
          </div>
          <div class="suggestion-bottom">
            <a class="suggestion-source" href="${safeUrl}" target="_blank" title="${escHtml(s.title)}">${escHtml(s.source)}</a>
            <button class="suggestion-use" onclick="applyPrice('${escHtml(item.id)}', ${s.price}, '${safeUrl}')">Use</button>
          </div>
        </div>`;
      }).join('')}
    </div>` : '';

  const searchBtn = priceSearchEnabled
    ? `<button class="price-search-btn" onclick="searchItemPrice('${escHtml(item.id)}')" title="Search for price">&#128269;</button>`
    : '';

  return `
    <tr class="${rowClass}" data-item-id="${escHtml(item.id)}">
      <td class="photo-cell col-photo" onclick="openLightbox('${escHtml(item.id)}')">
        <img src="${thumbUrl(currentSessionId, item.primary_photo)}" loading="lazy" alt="${escHtml(item.primary_photo)}">
        <span class="photo-name">${escHtml(item.primary_photo)}</span>
        ${confBadge}
      </td>
      <td class="editable col-category" data-field="category" data-item-id="${escHtml(item.id)}">${escHtml(item.category || '')}</td>
      <td class="editable col-manufacturer" data-field="manufacturer" data-item-id="${escHtml(item.id)}">${escHtml(item.manufacturer || '')}</td>
      <td class="editable col-item" data-field="item" data-item-id="${escHtml(item.id)}">${escHtml(item.item || '')}${flagHtml}</td>
      <td class="editable col-model" data-field="model_serial" data-item-id="${escHtml(item.id)}">${escHtml(item.model_serial || '')}</td>
      <td class="editable col-qty" data-field="quantity" data-item-id="${escHtml(item.id)}" style="text-align:center">${escHtml(String(item.quantity || 1))}</td>
      <td class="price-cell col-price" style="position:relative">
        <div class="price-input-wrap">
          <input type="number" class="price-input" min="0" step="0.01"
            value="${item.price != null ? item.price : ''}"
            placeholder="0.00"
            data-item-id="${escHtml(item.id)}" data-field="price"
            onchange="quickSave(this)"
            onblur="quickSave(this)">
          ${searchBtn}
        </div>
        ${suggestHtml}
      </td>
      <td class="total-cell col-total" id="total-${escHtml(item.id)}">${formatTotal(item.price, item.quantity)}</td>
      <td class="col-age">
        <input type="text" class="age-input"
          value="${item.age != null ? escHtml(String(item.age)) : ''}"
          placeholder="e.g. 3"
          data-item-id="${escHtml(item.id)}" data-field="age"
          onchange="quickSave(this)"
          onblur="quickSave(this)">
      </td>
    </tr>`;
}

// Set up editable cell click handler via delegation (call once after render)
document.addEventListener('DOMContentLoaded', () => {
  const tbody = document.getElementById('review-tbody');
  if (!tbody) return;
  tbody.addEventListener('click', e => {
    const cell = e.target.closest('.editable');
    if (cell && !cell.querySelector('input')) activateCell(cell);
  });
});

function activateCell(cell) {
  // For item field, find the text node to avoid capturing flag span text
  let current;
  if (cell.dataset.field === 'item') {
    const textNode = [...cell.childNodes].find(n => n.nodeType === Node.TEXT_NODE);
    current = textNode ? textNode.textContent.trim() : '';
  } else {
    current = cell.textContent.trim();
  }

  cell.innerHTML = `<input class="cell-edit" value="${escHtml(current)}">`;
  const input = cell.querySelector('input');
  input.focus();
  input.select();

  const finalize = async () => {
    const newVal  = input.value.trim();
    const itemId  = cell.dataset.itemId;
    const field   = cell.dataset.field;
    const saveVal = field === 'quantity' ? (parseInt(newVal) || 1) : newVal;

    try {
      await saveField(itemId, field, saveVal);
      updateItemInArray(itemId, { [field]: saveVal });
      updateRowTotal(itemId);
    } catch (err) {
      showToast('Could not save: ' + err.message, 'error');
    }

    const item = allItems.find(i => i.id === itemId);
    cell.textContent = item ? (item[field] != null ? String(item[field]) : '') : newVal;
  };

  let finalizing = false;
  input.addEventListener('blur', () => { if (!finalizing) { finalizing = true; finalize(); } });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); finalizing = true; finalize(); }
    if (e.key === 'Escape') { cell.textContent = current; }
    if (e.key === 'Tab') {
      e.preventDefault();
      finalizing = true;
      finalize().then(() => {
        const all = [...document.querySelectorAll('#review-tbody .editable')];
        const idx = all.indexOf(cell);
        if (idx < all.length - 1) activateCell(all[idx + 1]);
      });
    }
  });
}

async function quickSave(input) {
  const itemId = input.dataset.itemId;
  const field  = input.dataset.field;
  const val    = field === 'price'
    ? (input.value === '' ? null : parseFloat(input.value))
    : input.value.trim();

  try {
    await saveField(itemId, field, val);
    updateItemInArray(itemId, { [field]: val });
    updateRowTotal(itemId);
  } catch (_) {}
}

async function saveField(itemId, field, value) {
  await api('PUT', `/api/sessions/${currentSessionId}/items/${itemId}`, { [field]: value });
}

function updateItemInArray(itemId, updates) {
  const item = allItems.find(i => i.id === itemId);
  if (item) Object.assign(item, updates);
}

function updateRowTotal(itemId) {
  const item = allItems.find(i => i.id === itemId);
  const cell = document.getElementById('total-' + itemId);
  if (item && cell) cell.textContent = formatTotal(item.price, item.quantity);
}

// ── Price Search ──────────────────────────────────────────────────────────────
async function searchItemPrice(itemId) {
  const row = document.querySelector(`tr[data-item-id="${itemId}"]`);
  const priceCell = row ? row.querySelector('.price-cell') : null;

  // Clear any existing clarify UI / suggestions and show spinner
  if (priceCell) {
    const old = priceCell.querySelector('.price-suggestions, .price-clarify');
    if (old) old.remove();
    const btn = priceCell.querySelector('.price-search-btn');
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
  }

  try {
    const body = priceSearchContext[itemId] ? { messages: priceSearchContext[itemId] } : {};
    const res = await api('POST', `/api/sessions/${currentSessionId}/items/${itemId}/price-search`, body);

    if (res.question) {
      // Claude needs clarification — show inline Q&A
      priceSearchContext[itemId] = res.messages;
      _showClarifyUI(priceCell, itemId, res.question);
    } else if (res.suggestions && res.suggestions.length) {
      // Got suggestions
      delete priceSearchContext[itemId];
      updateItemInArray(itemId, { price_suggestions: res.suggestions });
      if (priceCell) {
        _updatePriceSuggestions(priceCell, itemId, res.suggestions);
      }
    } else {
      showToast('No prices found for this item.', 'info');
    }
  } catch (e) {
    showToast('Price search error: ' + e.message, 'error');
  } finally {
    if (priceCell) {
      const btn = priceCell.querySelector('.price-search-btn');
      if (btn) { btn.disabled = false; btn.textContent = '🔍'; }
    }
  }
}

function _showClarifyUI(priceCell, itemId, question) {
  if (!priceCell) return;
  const el = document.createElement('div');
  el.className = 'price-clarify';
  el.dataset.itemId = itemId;
  el.innerHTML = `
    <div class="clarify-question">&#10067; ${escHtml(question)}</div>
    <div class="clarify-input-wrap">
      <input class="clarify-input" type="text" placeholder="Your answer…">
      <button class="clarify-send" onclick="submitClarify('${escHtml(itemId)}')">&#8594;</button>
    </div>`;
  // Allow Enter key to submit
  el.querySelector('.clarify-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') submitClarify(itemId);
  });
  priceCell.appendChild(el);
}

async function submitClarify(itemId) {
  const row = document.querySelector(`tr[data-item-id="${itemId}"]`);
  const priceCell = row ? row.querySelector('.price-cell') : null;
  const clarifyEl = priceCell ? priceCell.querySelector('.price-clarify') : null;
  if (!clarifyEl) return;

  const input = clarifyEl.querySelector('.clarify-input');
  const answer = input ? input.value.trim() : '';
  if (!answer) return;

  // Append user's answer to the conversation context
  if (priceSearchContext[itemId]) {
    priceSearchContext[itemId] = priceSearchContext[itemId].concat([
      { role: 'user', content: answer }
    ]);
  }

  clarifyEl.remove();
  await searchItemPrice(itemId);
}

function _updatePriceSuggestions(priceCell, itemId, suggestions) {
  const existing = priceCell.querySelector('.price-suggestions');
  if (existing) existing.remove();
  if (!suggestions || !suggestions.length) return;

  const item = allItems.find(i => i.id === itemId);
  const temp = document.createElement('div');
  temp.innerHTML = buildRow(item);
  const newSugg = temp.querySelector('.price-suggestions');
  if (newSugg) priceCell.appendChild(newSugg);
}

async function searchAllPrices() {
  const btn = document.getElementById('btn-search-all');
  btn.disabled = true;
  btn.textContent = '&#128269; Searching...';
  try {
    const res = await api('POST', `/api/sessions/${currentSessionId}/price-search-all`);
    const data = await api('GET', `/api/sessions/${currentSessionId}`);
    allItems = data.items || [];
    renderReviewTable();
    showToast(`Found prices for ${res.updated} items`, 'success');
  } catch (e) {
    showToast('Price search failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '&#128269; Search All Prices';
  }
}

function applyPrice(itemId, price, sourceUrl) {
  const row = document.querySelector(`tr[data-item-id="${itemId}"]`);
  if (row) {
    const input = row.querySelector('.price-input');
    if (input) {
      input.value = price;
      quickSave(input);
    }
    // Save the source URL alongside the price
    if (sourceUrl) {
      api('PUT', `/api/sessions/${currentSessionId}/items/${itemId}`, { price_source_url: sourceUrl })
        .catch(() => {});
      updateItemInArray(itemId, { price_source_url: sourceUrl });
    }
    const sugg = row.querySelector('.price-suggestions');
    if (sugg) sugg.remove();
  }
}

// ── Export ────────────────────────────────────────────────────────────────────
async function exportInventory() {
  try {
    const session = sessionData || {};
    const folderPath = session.folder_path || '';
    const folderName = folderPath.split(/[/\\]/).filter(Boolean).pop() || 'Inventory';
    const defaultName = `${folderName} Inventory.xlsx`;

    const saveRes = await api('POST', '/api/browse-save', {
      default_dir: folderPath,
      default_name: defaultName,
    });
    if (!saveRes.path) return;

    const res = await api('POST', `/api/sessions/${currentSessionId}/export`, {
      output_path: saveRes.path,
    });
    showToast(`Saved: ${res.path.split(/[/\\]/).pop()}`, 'success', 4000);
  } catch (e) {
    showToast('Export failed: ' + e.message, 'error');
  }
}

// ── Lightbox ──────────────────────────────────────────────────────────────────
function openLightbox(itemId) {
  const item = allItems.find(i => i.id === itemId);
  if (!item) return;

  const photosDiv = document.getElementById('lightbox-photos');
  photosDiv.innerHTML = item.photos.map(p =>
    `<img src="${photoUrl(currentSessionId, p)}" alt="${escHtml(p)}">`
  ).join('');

  document.getElementById('lightbox-label').textContent =
    [item.manufacturer, item.item].filter(Boolean).join(' — ') || item.primary_photo;

  document.getElementById('lightbox').classList.remove('hidden');
}

function closeLightbox(e) {
  if (!e || e.target === document.getElementById('lightbox') || e.target.classList.contains('lightbox-close')) {
    document.getElementById('lightbox').classList.add('hidden');
    document.getElementById('lightbox-photos').innerHTML = '';
  }
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeLightbox({ target: document.getElementById('lightbox') });
});

// ── Start ─────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', init);
