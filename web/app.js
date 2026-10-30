// Market Journal front end. Plain JS, hash routes, talks to the local server.
const TOKEN = new URLSearchParams(location.search).get('t') || '';
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const view = () => $('#view');

let STATUS = { configured: false, sync: {}, series: [], platforms: [] };
let TAGS = [];
let redraws = [];
let renderSeq = 0;

// ---------------------------------------------------------------- helpers
function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function money(v, opts = {}) {
  if (v == null || isNaN(v)) return '—';
  const abs = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = v < 0 ? '−' : opts.plus && v > 0 ? '+' : '';
  return `${sign}$${abs}`;
}
const pnlClass = v => v > 0 ? 'pos' : v < 0 ? 'neg' : 'muted';
const moneyHtml = (v, opts) => `<span class="${pnlClass(v)}">${money(v, opts)}</span>`;
function cents(p) {
  if (p == null) return '—';
  const c = p * 100;
  return (Math.abs(c - Math.round(c)) < 0.05 ? Math.round(c) : c.toFixed(1)) + '¢';
}
const pct = v => v == null ? '—' : v.toFixed(1) + '%';
const ratio = v => v == null ? '—' : v.toFixed(2);
const qty = n => n == null ? '—' : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
function dur(s) {
  if (s == null) return '—';
  if (s < 60) return Math.round(s) + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm ' + String(Math.round(s % 60)).padStart(2, '0') + 's';
  if (s < 86400) return Math.floor(s / 3600) + 'h ' + Math.round((s % 3600) / 60) + 'm';
  return (s / 86400).toFixed(1) + ' d';
}
const mins = m => m == null ? '—' : m < 60 ? m.toFixed(1) + ' min' : m < 1440 ? (m / 60).toFixed(1) + ' h' : (m / 1440).toFixed(1) + ' d';
const dt = ts => ts ? new Date(ts * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
const dtFull = ts => ts ? new Date(ts * 1000).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' }) : '—';
const timeOnly = ts => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
const dayFmt = d => d.toLocaleDateString(undefined, d.getFullYear() === new Date().getFullYear() ? { month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric', year: 'numeric' });
const dateShort = ts => dayFmt(new Date(ts * 1000));
function iso(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function parseDay(s) { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); }
function addDays(s, n) { const d = parseDay(s); d.setDate(d.getDate() + n); return iso(d); }
function monday(s) { const d = parseDay(s); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return iso(d); }
const longDay = s => parseDay(s).toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
const shortDay = s => dayFmt(parseDay(s));
const sideBadge = s => `<span class="badge ${s === 'yes' || s === 'no' ? s : 'other'}">${esc(s.toUpperCase())}</span>`;
const PLATFORM = { kalshi: 'Kalshi', polymarket: 'Polymarket' };
function resultBadge(t) {
  if (t.status === 'open') return '<span class="badge open">OPEN</span>';
  return t.pnl > 0 ? '<span class="badge win">WIN</span>' : t.pnl < 0 ? '<span class="badge loss">LOSS</span>' : '<span class="badge">EVEN</span>';
}
const tagChips = tags => (tags || []).map(t => `<span class="chip ${t.kind}">${esc(t.name)}</span>`).join('');

async function api(path, params = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null));
  const res = await fetch(`/api/${path}${q.toString() ? '?' + q : ''}`, { headers: { 'X-Token': TOKEN } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `Request failed (${res.status})`);
  return body;
}
async function post(path, body = {}) {
  const res = await fetch(`/api/${path}`, { method: 'POST', headers: { 'X-Token': TOKEN, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 2600);
}
function store(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem('kj.' + key));
    localStorage.setItem('kj.' + key, JSON.stringify(value));
  } catch { return undefined; }
}
function chart(el, draw) { draw(); redraws.push(draw); }
function crumb(html) { $('#crumb').innerHTML = html; }
function autosave(textarea, label, save) {
  let timer;
  const run = async () => {
    clearTimeout(timer);
    try { await save(textarea.value); label.textContent = 'Saved ' + new Date().toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }); }
    catch (e) { label.textContent = e.message; }
  };
  textarea.addEventListener('input', () => { label.textContent = 'Editing…'; clearTimeout(timer); timer = setTimeout(run, 900); });
  textarea.addEventListener('blur', () => { if (label.textContent === 'Editing…') run(); });
}

// ---------------------------------------------------------------- filters
const PRESETS = [['all', 'All time'], ['today', 'Today'], ['week', 'This week'], ['month', 'This month'], ['30d', 'Last 30 days'], ['year', 'This year'], ['custom', 'Custom']];
let FILTERS = store('filters') || { preset: 'all' };

function presetRange(p) {
  const today = iso(new Date());
  switch (p) {
    case 'today': return [today, today];
    case 'week': return [monday(today), today];
    case 'month': return [today.slice(0, 8) + '01', today];
    case '30d': return [addDays(today, -29), today];
    case 'year': return [today.slice(0, 5) + '01-01', today];
    default: return [null, null];
  }
}
function filterParams(extra = {}) {
  const f = FILTERS;
  let [start, end] = f.preset === 'custom' ? [f.start, f.end] : presetRange(f.preset);
  return { start, end, platform: f.platform, series: f.series, side: f.side, outcome: f.outcome, tag: f.tag, search: f.search, status: f.status, ...extra };
}
function tagOptions(selected, blank = 'All tags') {
  const opt = t => `<option value="${t.id}" ${String(selected) === String(t.id) ? 'selected' : ''}>${esc(t.name)}</option>`;
  const tags = TAGS.filter(t => t.kind === 'tag'), mistakes = TAGS.filter(t => t.kind === 'mistake');
  return `<option value="">${blank}</option>` +
    (tags.length ? `<optgroup label="Tags">${tags.map(opt).join('')}</optgroup>` : '') +
    (mistakes.length ? `<optgroup label="Mistakes">${mistakes.map(opt).join('')}</optgroup>` : '');
}
function seriesOptions(selected) {
  const opt = s => `<option value="${esc(s.ticker)}" ${selected === s.ticker ? 'selected' : ''}>${esc(s.name)}</option>`;
  if (STATUS.platforms.length < 2) return STATUS.series.map(opt).join('');
  return STATUS.platforms.map(p => `<optgroup label="${esc(p.name)}">${STATUS.series.filter(s => s.platform === p.name).map(opt).join('')}</optgroup>`).join('');
}
function filterBar(onChange, opts = {}) {
  const f = FILTERS;
  const el = document.createElement('div');
  el.className = 'filters';
  el.innerHTML = `
    <div class="seg">${PRESETS.map(([k, l]) => `<button data-preset="${k}" class="${f.preset === k ? 'on' : ''}">${l}</button>`).join('')}</div>
    <span class="custom" ${f.preset === 'custom' ? '' : 'hidden'}>
      <input type="date" data-k="start" value="${esc(f.start || '')}"> – <input type="date" data-k="end" value="${esc(f.end || '')}">
    </span>
    ${STATUS.platforms.length > 1 ? `<select data-k="platform"><option value="">All platforms</option>${STATUS.platforms.map(p => `<option value="${p.id}" ${f.platform === p.id ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>` : ''}
    <select data-k="series"><option value="">All markets</option>${seriesOptions(f.series)}</select>
    <select data-k="side"><option value="">YES & NO</option><option value="yes" ${f.side === 'yes' ? 'selected' : ''}>YES only</option><option value="no" ${f.side === 'no' ? 'selected' : ''}>NO only</option></select>
    <select data-k="outcome"><option value="">Wins & losses</option><option value="win" ${f.outcome === 'win' ? 'selected' : ''}>Wins</option><option value="loss" ${f.outcome === 'loss' ? 'selected' : ''}>Losses</option></select>
    <select data-k="tag">${tagOptions(f.tag)}</select>
    ${opts.status ? `<select data-k="status"><option value="">Open & closed</option><option value="closed" ${f.status === 'closed' ? 'selected' : ''}>Closed</option><option value="open" ${f.status === 'open' ? 'selected' : ''}>Open</option></select>` : ''}
    ${opts.search ? `<input type="search" data-k="search" placeholder="Search market or notes" value="${esc(f.search || '')}">` : ''}
    <button class="btn ghost small" data-reset>Reset</button>`;
  const changed = () => { store('filters', FILTERS); onChange(); };
  $$('[data-preset]', el).forEach(b => b.onclick = () => {
    FILTERS.preset = b.dataset.preset;
    $$('[data-preset]', el).forEach(x => x.classList.toggle('on', x === b));
    $('.custom', el).hidden = FILTERS.preset !== 'custom';
    if (FILTERS.preset !== 'custom' || (FILTERS.start && FILTERS.end)) changed();
  });
  $$('[data-k]', el).forEach(i => {
    const ev = i.type === 'search' ? 'input' : 'change';
    let timer;
    i.addEventListener(ev, () => { FILTERS[i.dataset.k] = i.value || undefined; clearTimeout(timer); timer = setTimeout(changed, i.type === 'search' ? 300 : 0); });
  });
  $('[data-reset]', el).onclick = () => { FILTERS = { preset: 'all' }; store('filters', FILTERS); route(); };
  return el;
}

// ---------------------------------------------------------------- status / sync
async function refreshStatus() {
  try {
    const before = STATUS.sync && STATUS.sync.running;
    STATUS = await api('status');
    renderStatus();
    if (before && !STATUS.sync.running) {
      if (STATUS.sync.error) toast('Sync failed'); else toast(STATUS.sync.message || 'Sync complete');
      TAGS = (await api('tags')).tags;
      route();
    }
  } catch (e) { /* server restarting; try again shortly */ }
  clearTimeout(refreshStatus.timer);
  refreshStatus.timer = setTimeout(refreshStatus, STATUS.sync && STATUS.sync.running ? 1200 : 15000);
}
function renderStatus() {
  const s = STATUS, sync = s.sync || {};
  const btn = $('#sync-btn');
  btn.disabled = !!sync.running || !s.configured;
  btn.textContent = sync.running ? 'Syncing…' : 'Sync';
  const msg = $('#sync-msg');
  msg.classList.toggle('err', !!sync.error);
  msg.textContent = sync.running ? sync.message : sync.error ? sync.error : s.last_sync ? 'Synced ' + dt(s.last_sync) : '';
  const parts = [];
  const label = s.polymarket ? 'Kalshi ' : '';
  if (s.balance) parts.push(`${label}cash <b>${money(s.balance.cash)}</b> · positions <b>${money(s.balance.portfolio)}</b>`);
  if (s.polymarket && s.pm_value != null) parts.push(`Polymarket positions <b>${money(s.pm_value)}</b>`);
  $('#balance').innerHTML = parts.join(' · ').replace(/^./, c => c.toUpperCase());
  const banner = $('#banner');
  if (!s.configured && !location.hash.startsWith('#/settings')) {
    banner.innerHTML = `<div class="banner"><span>Connect Kalshi or Polymarket to start your journal.</span><a class="btn small" href="#/settings">Connect an account</a></div>`;
  } else if (sync.error && !sync.running) {
    banner.innerHTML = `<div class="banner err"><span>${esc(sync.error)}</span><span class="row"><button class="btn small" id="retry">Retry</button><a class="btn small" href="#/settings">Settings</a></span></div>`;
    $('#retry').onclick = startSync;
  } else banner.innerHTML = '';
}
async function startSync() {
  try { await post('sync'); STATUS.sync = { running: true, message: 'Starting sync' }; renderStatus(); refreshStatus(); }
  catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- shared pieces
function summaryCards(s, opts = {}) {
  const cards = [
    ['Net P&L', moneyHtml(s.net_pnl, { plus: true }), `${money(s.gross_pnl)} before fees`, 'big'],
    ['Win rate', pct(s.win_rate), `${s.wins} W · ${s.losses} L${s.scratches ? ' · ' + s.scratches + ' even' : ''}`],
    ['Trades', s.trades, `${qty(s.contracts)} contracts`],
    ['Profit factor', ratio(s.profit_factor), 'gross wins ÷ gross losses'],
    ['Expectancy', moneyHtml(s.expectancy), s.per_contract == null ? 'per trade' : `per trade · ${(s.per_contract * 100).toFixed(1)}¢ per contract`],
    ['Avg win / loss', `<span class="pos">${money(s.avg_win)}</span> <span class="faint">/</span> <span class="neg">${money(s.avg_loss)}</span>`, s.avg_win && s.avg_loss ? `ratio ${(s.avg_win / -s.avg_loss).toFixed(2)}` : ''],
    ['Fees paid', money(s.fees), s.gross_pnl ? `${(s.fees / Math.abs(s.gross_pnl) * 100).toFixed(0)}% of gross P&L` : ''],
    ['Max drawdown', s.max_drawdown ? `<span class="neg">${money(-s.max_drawdown)}</span>` : '—', 'largest drop from a peak'],
  ];
  if (opts.more) cards.push(
    ['Largest win', moneyHtml(s.largest_win), ''], ['Largest loss', moneyHtml(s.largest_loss), ''],
    ['Streak', s.streak > 0 ? `<span class="pos">${s.streak} W</span>` : s.streak < 0 ? `<span class="neg">${-s.streak} L</span>` : '—', `best ${s.longest_win_streak} W · worst ${s.longest_loss_streak} L`],
    ['Avg hold', dur(s.avg_hold), '']);
  return `<div class="cards c8">${cards.map(([k, v, sub, cls]) => `<div class="card ${cls || ''}"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${sub || ''}</div></div>`).join('')}</div>`;
}
function tradeTable(trades, opts = {}) {
  if (!trades.length) return `<div class="empty">${opts.empty || 'No trades match these filters.'}</div>`;
  const cols = opts.compact
    ? ['Closed', 'Market', 'Side', 'Qty', 'Entry', 'P&L']
    : ['Opened', 'Market', 'Side', 'Qty', 'Entry', 'Exit', 'Hold', 'Fees', 'P&L', 'Result', 'Tags'];
  const row = t => {
    const cells = opts.compact
      ? [dt(t.close_ts || t.open_ts), mkt(t), sideBadge(t.side), qty(t.contracts), cents(t.entry_price), moneyHtml(t.pnl, { plus: true })]
      : [dt(t.open_ts), mkt(t), sideBadge(t.side), qty(t.contracts), cents(t.entry_price),
        t.status === 'open' ? '—' : cents(t.exit_price) + (t.close_reason === 'settled' ? ' <span class="faint" title="Held to settlement">⚑</span>' : ''),
        dur(t.hold_seconds), money(t.fees), t.status === 'open' ? '<span class="faint">open</span>' : moneyHtml(t.pnl, { plus: true }), resultBadge(t),
        tagChips(t.tags) + (t.note ? ' <span title="Has a note" class="faint">✎</span>' : '')];
    const numeric = opts.compact ? [3, 4, 5] : [3, 4, 5, 6, 7, 8];
    return `<tr class="click" data-id="${esc(t.id)}">${cells.map((c, i) => `<td class="${numeric.includes(i) ? 'num' : ''}">${c}</td>`).join('')}</tr>`;
  };
  const numericHead = opts.compact ? [3, 4, 5] : [3, 4, 5, 6, 7, 8];
  return `<table class="t"><thead><tr>${cols.map((c, i) => `<th class="${numericHead.includes(i) ? 'num' : ''} ${opts.sortable ? 'sortable' : ''}" data-col="${i}">${c}</th>`).join('')}</tr></thead>
    <tbody>${trades.map(row).join('')}</tbody></table>`;
}
function mkt(t) {
  const where = STATUS.platforms.length > 1 ? `${PLATFORM[t.platform] || 'Kalshi'} · ` : '';
  return `<div class="mkt"><div class="ttl" title="${esc(t.title)}">${esc(t.title)}</div><div class="tk" title="${esc(t.ticker)}">${where}${esc(t.series_name || t.ticker)}</div></div>`;
}
function wireRows(root) { $$('tr[data-id]', root).forEach(tr => tr.onclick = () => { location.hash = '#/trade/' + encodeURIComponent(tr.dataset.id); }); }
function needsSetup() {
  if (STATUS.configured || STATUS.trades) return false;
  view().innerHTML = `<div class="panel"><div class="empty"><h2>Welcome to Market Journal</h2>
    <p>Connect Kalshi with an API key, or Polymarket with your wallet address, and your trades will appear here automatically.<br>
    The journal only reads your history. It never places or cancels orders.</p>
    <a class="btn primary" href="#/settings">Connect an account</a></div></div>`;
  return true;
}
function syncingEmpty() {
  return STATUS.sync && STATUS.sync.running
    ? `<div class="panel"><div class="empty"><h2>Downloading your trade history…</h2><p>${esc(STATUS.sync.message || '')}</p></div></div>`
    : `<div class="panel"><div class="empty"><h2>No trades yet</h2><p>Once you trade, click Sync to pull your history in.</p><button class="btn primary" onclick="startSync()">Sync now</button></div></div>`;
}

// ---------------------------------------------------------------- dashboard
async function viewDashboard(seq) {
  crumb('Dashboard');
  if (needsSetup()) return;
  const v = view();
  v.innerHTML = '';
  v.appendChild(filterBar(() => viewDashboard(++renderSeq)));
  const body = document.createElement('div');
  v.appendChild(body);
  const d = await api('dashboard', filterParams());
  if (seq !== renderSeq) return;
  if (!d.summary.trades && !d.open.length) { body.innerHTML = syncingEmpty(); return; }
  body.innerHTML = `
    ${summaryCards(d.summary)}
    <div class="grid g3" style="margin-top:16px">
      <div class="panel"><div class="hd"><h2>Equity curve</h2><span class="legend">Cumulative net P&L after fees</span></div><div class="bd"><div id="eq"></div></div></div>
      <div class="panel"><div class="hd"><h2>By market</h2><a href="#/reports">All reports →</a></div><div class="bd flush">
        <table class="t"><thead><tr><th>Market</th><th class="num">Trades</th><th class="num">Win %</th><th class="num">Net</th></tr></thead><tbody>
        ${d.by_series.map(r => `<tr><td>${esc(r.label)}</td><td class="num">${r.trades}</td><td class="num">${pct(r.win_rate)}</td><td class="num">${moneyHtml(r.net_pnl)}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">No closed trades</td></tr>'}
        </tbody></table></div></div>
    </div>
    <div class="grid g2" style="margin-top:16px">
      <div class="panel"><div class="hd"><h2>Daily P&L</h2><span class="legend">Last 30 trading days · click a bar to open the day</span></div><div class="bd"><div id="daily"></div></div></div>
      <div class="panel"><div class="hd"><h2>Recent trades</h2><a href="#/trades">All trades →</a></div><div class="bd flush" id="recent">${tradeTable(d.recent, { compact: true })}</div></div>
    </div>
    ${d.open.length ? `<div class="panel" style="margin-top:16px"><div class="hd"><h2>Open positions</h2><span class="legend">${d.open.length} open</span></div><div class="bd flush" id="open">${tradeTable(d.open)}</div></div>` : ''}`;
  wireRows(body);
  chart($('#eq'), () => Charts.line($('#eq'), d.equity.map((p, i) => ({ x: i, t: p.t, y: p.equity, id: p.id, tip: `${dtFull(p.t)}<br><b>${money(p.equity, { plus: true })}</b>${p.drawdown < 0 ? ` · drawdown ${money(p.drawdown)}` : ''}` })),
    { height: 280, yFmt: v => money(v), xFmt: i => dateShort(d.equity[i].t), onClick: p => { location.hash = '#/trade/' + encodeURIComponent(p.id); } }));
  chart($('#daily'), () => Charts.bars($('#daily'), d.days.map(x => ({ label: shortDay(x.day), value: x.pnl, day: x.day, tip: `${longDay(x.day)}<br><b>${money(x.pnl, { plus: true })}</b> · ${x.trades} trades · ${x.wins} wins` })),
    { height: 220, fmt: v => money(v), onClick: it => { location.hash = '#/day/' + it.day; } }));
}

// ---------------------------------------------------------------- trades
let tradeSort = store('tradeSort') || { col: 0, dir: -1 };
async function viewTrades(seq) {
  crumb('Trades');
  if (needsSetup()) return;
  const v = view();
  v.innerHTML = '';
  const bar = filterBar(() => viewTrades(++renderSeq), { status: true, search: true });
  const exp = document.createElement('button');
  exp.className = 'btn small';
  exp.textContent = 'Export CSV';
  exp.onclick = () => {
    const q = new URLSearchParams(Object.entries(filterParams()).filter(([, x]) => x));
    q.set('t', TOKEN);
    location.href = '/api/export.csv?' + q;
  };
  bar.appendChild(exp);
  v.appendChild(bar);
  const body = document.createElement('div');
  v.appendChild(body);
  const d = await api('trades', filterParams());
  if (seq !== renderSeq) return;
  if (!d.trades.length && FILTERS.preset === 'all' && !FILTERS.series && !FILTERS.tag && !FILTERS.search) { body.innerHTML = syncingEmpty(); return; }
  const keys = [t => t.open_ts, t => t.title, t => t.side, t => t.contracts, t => t.entry_price, t => t.exit_price ?? -1, t => t.hold_seconds ?? -1, t => t.fees, t => t.status === 'open' ? -1e9 : t.pnl, t => t.pnl, t => (t.tags[0] || {}).name || ''];
  let shown = 150;
  const render = () => {
    const key = keys[tradeSort.col] || keys[0];
    const sorted = [...d.trades].sort((a, b) => { const x = key(a), y = key(b); return (x > y ? 1 : x < y ? -1 : 0) * tradeSort.dir; });
    const s = d.summary;
    body.innerHTML = `
      <div class="row muted" style="margin-bottom:10px">
        <b class="${pnlClass(s.net_pnl)}">${money(s.net_pnl, { plus: true })}</b> net ·
        ${s.trades} closed · ${pct(s.win_rate)} win rate · ${money(s.fees)} fees ·
        ${d.trades.length - s.trades} open
      </div>
      <div class="panel"><div class="bd flush">${tradeTable(sorted.slice(0, shown), { sortable: true })}</div>
      ${sorted.length > shown ? `<div class="list-more"><button class="btn" id="more">Show more (${sorted.length - shown} left)</button></div>` : ''}</div>`;
    wireRows(body);
    $$('th[data-col]', body).forEach(th => {
      if (+th.dataset.col === tradeSort.col) th.textContent += tradeSort.dir > 0 ? ' ▲' : ' ▼';
      th.onclick = () => {
        const c = +th.dataset.col;
        tradeSort = { col: c, dir: tradeSort.col === c ? -tradeSort.dir : -1 };
        store('tradeSort', tradeSort);
        render();
      };
    });
    const more = $('#more', body);
    if (more) more.onclick = () => { shown += 300; render(); };
  };
  render();
}

// ---------------------------------------------------------------- trade detail
async function viewTrade(seq, id) {
  crumb('<a href="#/trades">Trades</a> / Trade');
  const v = view();
  const { trade: t, market: m } = await api('trade', { id });
  if (seq !== renderSeq) return;
  crumb(`<a href="#/trades">Trades</a> / ${esc(t.series_name || t.ticker)}`);
  const sideWord = t.side.toUpperCase();
  const outcome = t.market_result ? `Market settled <b>${esc(t.market_result.toUpperCase())}</b>` : 'Market not settled yet';
  const where = PLATFORM[t.platform] || 'Kalshi';
  v.innerHTML = `
    <div class="panel"><div class="bd">
      <div class="row between">
        <div>
          <div class="row">${sideBadge(t.side)} ${resultBadge(t)} ${t.close_reason === 'settled' ? '<span class="chip">Held to settlement</span>' : t.close_reason === 'sold' ? '<span class="chip">Sold before close</span>' : ''}</div>
          <h1 style="margin-top:8px">${esc(t.title)}</h1>
          <div class="muted" style="margin-top:2px">${where} · ${esc(m && m.subtitle ? m.subtitle + ' · ' : '')}<span class="mono">${esc(t.ticker)}</span> · ${outcome}</div>
        </div>
        <div style="text-align:right">
          <div class="muted">Net P&L</div>
          <div style="font-size:32px;font-weight:700" class="${pnlClass(t.pnl)}">${t.status === 'open' ? '<span class="faint">Open</span>' : money(t.pnl, { plus: true })}</div>
          <div class="muted">${t.status === 'open' ? '' : `${t.return_pct >= 0 ? '+' : ''}${t.return_pct.toFixed(1)}% on ${money(t.cost)} risked`}</div>
        </div>
      </div>
      <div class="kv" style="margin-top:18px">
        <div><div class="k">Opened</div><div class="v">${dtFull(t.open_ts)}</div></div>
        <div><div class="k">Closed</div><div class="v">${t.close_ts ? dtFull(t.close_ts) : '—'}</div></div>
        <div><div class="k">Contracts</div><div class="v">${qty(t.contracts)} ${sideWord}</div></div>
        <div><div class="k">Avg entry</div><div class="v">${cents(t.entry_price)}</div></div>
        <div><div class="k">Avg exit</div><div class="v">${t.exit_price == null ? '—' : cents(t.exit_price)}</div></div>
        <div><div class="k">Cost</div><div class="v">${money(t.cost)}</div></div>
        <div><div class="k">Fees</div><div class="v">${money(t.fees)}</div></div>
        <div><div class="k">P&L before fees</div><div class="v ${pnlClass(t.pnl_gross)}">${t.status === 'open' ? '—' : money(t.pnl_gross, { plus: true })}</div></div>
        <div><div class="k">Held</div><div class="v">${dur(t.hold_seconds)}</div></div>
        <div><div class="k">Time left at entry</div><div class="v">${mins(t.minutes_left)}</div></div>
        <div><div class="k">Fills</div><div class="v">${t.fills.length}${t.taker_fills + t.maker_fills ? ` <span class="faint">(${t.taker_fills} taker · ${t.maker_fills} maker)</span>` : ''}</div></div>
        <div><div class="k">Market close</div><div class="v">${m && m.close_ts ? dtFull(m.close_ts) : '—'}</div></div>
      </div>
    </div></div>
    <div class="panel"><div class="hd"><h2>Price chart</h2><span class="legend"><span><i style="background:var(--accent)"></i>${sideWord} price</span><span><i style="background:var(--win)"></i>Buy</span><span><i style="background:var(--loss)"></i>Sell</span><span><i style="background:var(--warn)"></i>Settlement</span></span></div>
      <div class="bd"><div id="pc"><div class="muted">Loading price history…</div></div></div></div>
    <div class="grid g2">
      <div class="panel"><div class="hd"><h2>Notes</h2><span class="saved" id="note-saved">${t.note ? 'Saved' : ''}</span></div>
        <div class="bd"><textarea class="note" id="note" placeholder="Why did you take this trade? What was your plan? How did you feel? What would you do differently?">${esc(t.note)}</textarea></div></div>
      <div class="panel"><div class="hd"><h2>Tags & mistakes</h2></div><div class="bd" id="tagbox"></div></div>
    </div>
    <div class="panel"><div class="hd"><h2>Fills</h2></div><div class="bd flush">
      <table class="t"><thead><tr><th>Time</th><th>Action</th><th class="num">Contracts</th><th class="num">Price</th><th class="num">Value</th><th class="num">Fee</th><th>Liquidity</th></tr></thead><tbody>
      ${t.fills.map(f => `<tr><td>${dtFull(f.ts)}</td><td>${f.kind === 'split' ? '<span class="pos">Split</span>' : f.kind === 'merge' ? '<span class="neg">Merge</span>' : f.action === 'buy' ? '<span class="pos">Buy</span>' : '<span class="neg">Sell</span>'} ${esc(f.side.toUpperCase())}</td><td class="num">${qty(f.qty)}</td><td class="num">${cents(f.price)}</td><td class="num">${money(f.qty * f.price)}</td><td class="num">${money(f.fee)}</td><td>${f.is_taker == null ? '<span class="faint">—</span>' : f.is_taker ? 'Taker' : 'Maker'}</td></tr>`).join('')}
      ${t.close_reason === 'settled' ? `<tr><td>${dtFull(t.close_ts)}</td><td><span class="muted">Settled</span></td><td class="num">${qty(t.settled_qty)}</td><td class="num">${cents(t.settled_qty ? t.payout / t.settled_qty : 0)}</td><td class="num">${money(t.payout)}</td><td class="num"></td><td></td></tr>` : ''}
      </tbody></table></div></div>`;
  autosave($('#note'), $('#note-saved'), note => post('trade/note', { id: t.id, note }));
  renderTagBox($('#tagbox'), t);
  try {
    const { points, direct } = await api('candles', { id: t.id });
    if (seq !== renderSeq) return;
    const conv = y => direct || t.side === 'yes' ? y * 100 : (1 - y) * 100;  // Polymarket prices are already the held outcome's
    const pts = points.map(p => ({ t: p.t, v: p.mid ?? p.last })).filter(p => p.v != null).map(p => ({ t: p.t, v: conv(p.v) }));
    pts.forEach(p => { p.tip = `${dtFull(p.t)}<br>${sideWord} ${p.v.toFixed(1)}¢`; });
    const marks = t.fills.map(f => ({ t: f.ts, v: f.price * 100, kind: f.action, tip: `${f.action === 'buy' ? 'Bought' : 'Sold'} ${qty(f.qty)} ${sideWord} @ ${cents(f.price)}<br>${dtFull(f.ts)}` }));
    if (t.close_reason === 'settled' && t.settled_qty) marks.push({ t: t.close_ts, v: t.payout / t.settled_qty * 100, kind: 'settle', tip: `Settled ${qty(t.settled_qty)} @ ${cents(t.payout / t.settled_qty)}<br>${dtFull(t.close_ts)}` });
    const span = (pts.length ? pts[pts.length - 1].t - pts[0].t : 0);
    const xFmt = span > 2 * 86400 ? dateShort : timeOnly;
    chart($('#pc'), () => Charts.price($('#pc'), pts, marks, { height: 320, xFmt, band: [t.open_ts, t.close_ts || Date.now() / 1000] }));
  } catch (e) {
    $('#pc').innerHTML = `<div class="muted">Price history unavailable: ${esc(e.message)}</div>`;
  }
}
function renderTagBox(box, t) {
  const on = new Set(t.tags.map(x => x.id));
  const section = (kind, title, hint) => `
    <div style="margin-bottom:14px">
      <h3 style="margin-bottom:6px">${title}</h3>
      <div>${TAGS.filter(x => x.kind === kind).map(x => `<span class="chip toggle ${kind} ${on.has(x.id) ? 'on' : ''}" data-tag="${x.id}">${esc(x.name)}</span>`).join('') || `<span class="faint">${hint}</span>`}</div>
      <div class="row" style="margin-top:8px"><input data-new="${kind}" placeholder="New ${kind === 'tag' ? 'tag' : 'mistake'}…" style="border:1px solid var(--line-2);border-radius:8px;padding:5px 9px;background:var(--panel)"><button class="btn small" data-add="${kind}">Add</button></div>
    </div>`;
  box.innerHTML = section('tag', 'Tags', 'e.g. "followed plan", "breakout", "news"') + section('mistake', 'Mistakes', 'e.g. "FOMO", "oversized", "chased price"');
  const save = async () => {
    await post('trade/tags', { id: t.id, tag_ids: [...on] });
    t.tags = TAGS.filter(x => on.has(x.id));
  };
  $$('[data-tag]', box).forEach(c => c.onclick = async () => {
    const id = +c.dataset.tag;
    on.has(id) ? on.delete(id) : on.add(id);
    c.classList.toggle('on', on.has(id));
    try { await save(); } catch (e) { toast(e.message); }
  });
  const add = async kind => {
    const input = $(`[data-new="${kind}"]`, box);
    if (!input.value.trim()) return;
    try {
      const { tag } = await post('tags', { name: input.value, kind });
      TAGS = (await api('tags')).tags;
      on.add(tag.id);
      await save();
      renderTagBox(box, t);
    } catch (e) { toast(e.message); }
  };
  $$('[data-add]', box).forEach(b => b.onclick = () => add(b.dataset.add));
  $$('[data-new]', box).forEach(i => i.onkeydown = e => { if (e.key === 'Enter') add(i.dataset.new); });
}

// ---------------------------------------------------------------- calendar
async function viewCalendar(seq, ym) {
  crumb('Calendar');
  if (needsSetup()) return;
  const today = new Date();
  let [year, month] = ym ? ym.split('-').map(Number) : [today.getFullYear(), today.getMonth() + 1];
  const d = await api('calendar', { year, month });
  if (seq !== renderSeq) return;
  const title = new Date(year, month - 1, 1).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  const prev = month === 1 ? `${year - 1}-12` : `${year}-${String(month - 1).padStart(2, '0')}`;
  const next = month === 12 ? `${year + 1}-01` : `${year}-${String(month + 1).padStart(2, '0')}`;
  const todayIso = iso(today);
  const maxAbs = Math.max(1e-9, ...d.days.map(c => Math.abs(c.pnl || 0)));
  const cells = [];
  for (let i = 0; i < d.first_weekday; i++) cells.push(null);
  d.days.forEach(c => cells.push(c));
  while (cells.length % 7) cells.push(null);
  let html = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun', 'Week'].map(x => `<div class="dow">${x}</div>`).join('');
  for (let r = 0; r < cells.length; r += 7) {
    const row = cells.slice(r, r + 7);
    let wk = 0, wkTrades = 0;
    row.forEach(c => {
      if (!c) { html += '<div class="cell blank"></div>'; return; }
      wk += c.pnl || 0; wkTrades += c.trades;
      const a = c.pnl == null ? 0 : .18 + .55 * Math.abs(c.pnl) / maxAbs;
      const bg = c.pnl == null ? '' : `background: color-mix(in srgb, ${c.pnl >= 0 ? 'var(--win)' : 'var(--loss)'} ${Math.round(a * 100 * .45)}%, var(--panel));`;
      html += `<div class="cell ${c.day === todayIso ? 'today' : ''}" style="${bg}" data-day="${c.day}">
        ${c.has_note ? '<span class="dot" title="Has a journal entry"></span>' : ''}
        <div class="d">${+c.day.slice(8)}</div>
        ${c.trades ? `<div class="p ${pnlClass(c.pnl)}">${money(c.pnl, { plus: true })}</div><div class="n">${c.trades} trade${c.trades === 1 ? '' : 's'} · ${Math.round(c.wins / c.trades * 100)}% W</div>` : ''}
      </div>`;
    });
    const first = row.find(Boolean);
    html += `<div class="wk"><div class="k">Week</div><div class="${pnlClass(wk)}" style="font-weight:600">${wkTrades ? money(wk, { plus: true }) : '—'}</div>
      ${first ? `<a href="#/weekly/${monday(first.day)}" style="font-size:12px">Review →</a>` : ''}</div>`;
  }
  view().innerHTML = `
    <div class="row between">
      <div class="row"><button class="btn" id="prev">‹</button><h1 style="min-width:200px;text-align:center">${title}</h1><button class="btn" id="next">›</button><button class="btn ghost" id="today">Today</button></div>
      <div class="muted">Days are by close time, local timezone</div>
    </div>
    ${summaryCards(d.summary)}
    <div class="cal">${html}</div>`;
  $('#prev').onclick = () => { location.hash = '#/calendar/' + prev; };
  $('#next').onclick = () => { location.hash = '#/calendar/' + next; };
  $('#today').onclick = () => { location.hash = '#/calendar'; };
  $$('[data-day]').forEach(c => c.onclick = () => { location.hash = '#/day/' + c.dataset.day; });
}

async function viewDay(seq, day) {
  day = day || iso(new Date());
  crumb(`<a href="#/calendar/${day.slice(0, 7)}">Calendar</a> / ${shortDay(day)}`);
  const d = await api('day', { day });
  if (seq !== renderSeq) return;
  view().innerHTML = `
    <div class="row between">
      <div class="row"><button class="btn" id="prev">‹</button><h1>${longDay(day)}</h1><button class="btn" id="next">›</button></div>
      <a class="btn" href="#/weekly/${monday(day)}">Weekly review →</a>
    </div>
    ${summaryCards(d.summary)}
    <div class="grid g3">
      <div class="panel"><div class="hd"><h2>Trades closed this day</h2></div><div class="bd flush">${tradeTable(d.trades, { empty: 'No trades closed on this day.' })}</div></div>
      <div class="panel"><div class="hd"><h2>Daily journal</h2><span class="saved" id="ds">${d.note ? 'Saved' : ''}</span></div>
        <div class="bd"><textarea class="note" id="dn" style="min-height:260px" placeholder="How did the day go? Market conditions, your mindset, what you learned…">${esc(d.note)}</textarea></div></div>
    </div>`;
  wireRows(view());
  $('#prev').onclick = () => { location.hash = '#/day/' + addDays(day, -1); };
  $('#next').onclick = () => { location.hash = '#/day/' + addDays(day, 1); };
  autosave($('#dn'), $('#ds'), note => post('day/note', { day, note }));
}

// ---------------------------------------------------------------- reports
const REPORTS = [['platform', 'Platform'], ['series', 'Market'], ['side', 'YES / NO'], ['tag', 'Tag'], ['mistake', 'Mistake'], ['entry_price', 'Entry price'],
  ['minutes_left', 'Time left at entry'], ['hold', 'Hold time'], ['size', 'Position size'], ['hour', 'Hour of day'], ['weekday', 'Day of week'],
  ['exit', 'Exit type'], ['liquidity', 'Maker / taker']];
async function viewReports(seq) {
  crumb('Reports');
  if (needsSetup()) return;
  const v = view();
  v.innerHTML = '';
  v.appendChild(filterBar(() => viewReports(++renderSeq)));
  const group = store('report') || 'series';
  const tabs = document.createElement('div');
  tabs.className = 'tabs';
  tabs.innerHTML = REPORTS.map(([k, l]) => `<button data-g="${k}" class="${k === group ? 'on' : ''}">${l}</button>`).join('');
  tabs.onclick = e => { const g = e.target.dataset.g; if (g) { store('report', g); viewReports(++renderSeq); } };
  v.appendChild(tabs);
  const body = document.createElement('div');
  v.appendChild(body);
  const d = await api('reports', filterParams({ group }));
  if (seq !== renderSeq) return;
  const label = REPORTS.find(r => r[0] === group)[1];
  body.innerHTML = `
    <div class="panel"><div class="hd"><h2>Net P&L by ${label.toLowerCase()}</h2><span class="legend">${d.summary.trades} closed trades</span></div>
      <div class="bd"><div id="rb"></div></div>
      <div class="bd flush"><table class="t"><thead><tr><th>${label}</th><th class="num">Trades</th><th class="num">Win %</th><th class="num">Net P&L</th><th class="num">Before fees</th><th class="num">Fees</th><th class="num">Per trade</th><th class="num">Per contract</th><th class="num">Profit factor</th><th class="num">Avg win</th><th class="num">Avg loss</th></tr></thead><tbody>
      ${d.rows.map(r => `<tr><td>${esc(r.label)}</td><td class="num">${r.trades}</td><td class="num">${pct(r.win_rate)}</td><td class="num">${moneyHtml(r.net_pnl)}</td><td class="num">${moneyHtml(r.gross_pnl)}</td><td class="num">${money(r.fees)}</td><td class="num">${moneyHtml(r.expectancy)}</td><td class="num">${r.per_contract == null ? '—' : (r.per_contract * 100).toFixed(1) + '¢'}</td><td class="num">${ratio(r.profit_factor)}</td><td class="num">${money(r.avg_win)}</td><td class="num">${money(r.avg_loss)}</td></tr>`).join('') || '<tr><td colspan="11" class="muted">No closed trades</td></tr>'}
      </tbody></table></div></div>
    <div class="grid g2" style="margin-top:16px">
      <div class="panel"><div class="hd"><h2>Price vs. outcome</h2><span class="legend"><span><i style="background:var(--faint)"></i>Price paid (implied odds)</span><span><i style="background:var(--accent)"></i>How often your side won</span></span></div>
        <div class="bd"><div id="cal"></div><p class="muted" style="margin:10px 0 0">If the teal bar is taller than the grey one, the contracts you bought at that price won more often than the price implied. Weighted by contracts.</p></div>
        <div class="bd flush"><table class="t"><thead><tr><th>Entry price</th><th class="num">Trades</th><th class="num">Implied</th><th class="num">Actual</th><th class="num">Difference</th></tr></thead><tbody>
        ${d.calibration.map(r => `<tr><td>${r.label}</td><td class="num">${r.trades}</td><td class="num">${pct(r.implied)}</td><td class="num">${pct(r.actual)}</td><td class="num ${pnlClass(r.edge)}">${r.edge >= 0 ? '+' : ''}${r.edge.toFixed(1)} pts</td></tr>`).join('') || '<tr><td colspan="5" class="muted">Needs trades on settled markets</td></tr>'}
        </tbody></table></div></div>
      <div class="panel"><div class="hd"><h2>Compare</h2><span class="legend">Two date ranges or tags side by side</span></div><div class="bd" id="cmp"></div></div>
    </div>`;
  chart($('#rb'), () => Charts.bars($('#rb'), d.rows.map(r => ({ label: r.label.length > 12 ? r.label.slice(0, 11) + '…' : r.label, value: r.net_pnl, tip: `${esc(r.label)}<br><b>${money(r.net_pnl, { plus: true })}</b> · ${r.trades} trades · ${pct(r.win_rate)} wins` })), { height: 240, fmt: v => money(v) }));
  chart($('#cal'), () => Charts.grouped($('#cal'), d.calibration.map(r => ({ label: r.label, a: r.implied, b: r.actual, tip: `${r.label}: paid ${pct(r.implied)}, won ${pct(r.actual)} (${r.trades} trades)` })), { max: 100, fmt: v => v + '%' }));
  renderCompare($('#cmp'));
}
function renderCompare(box) {
  const saved = store('compare') || { a: { preset: 'week' }, b: { preset: 'lastweek' } };
  const presets = [['all', 'All time'], ['week', 'This week'], ['lastweek', 'Last week'], ['month', 'This month'], ['lastmonth', 'Last month'], ['30d', 'Last 30 days'], ['custom', 'Custom']];
  const range = p => {
    const today = iso(new Date());
    if (p.preset === 'lastweek') { const m = monday(today); return [addDays(m, -7), addDays(m, -1)]; }
    if (p.preset === 'lastmonth') { const first = today.slice(0, 8) + '01'; const end = addDays(first, -1); return [end.slice(0, 8) + '01', end]; }
    if (p.preset === 'custom') return [p.start, p.end];
    return presetRange(p.preset);
  };
  const side = k => {
    const p = saved[k];
    return `<div><h3 style="margin-bottom:8px">Set ${k.toUpperCase()}</h3>
      <div class="filters" style="flex-direction:column;align-items:stretch">
        <select data-s="${k}" data-f="preset">${presets.map(([v, l]) => `<option value="${v}" ${p.preset === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <div class="row" ${p.preset === 'custom' ? '' : 'hidden'} data-custom="${k}"><input type="date" data-s="${k}" data-f="start" value="${p.start || ''}"><input type="date" data-s="${k}" data-f="end" value="${p.end || ''}"></div>
        <select data-s="${k}" data-f="tag">${tagOptions(p.tag, 'Any tag')}</select>
        <select data-s="${k}" data-f="side"><option value="">YES & NO</option><option value="yes" ${p.side === 'yes' ? 'selected' : ''}>YES</option><option value="no" ${p.side === 'no' ? 'selected' : ''}>NO</option></select>
      </div></div>`;
  };
  box.innerHTML = `<div class="grid g2">${side('a')}${side('b')}</div><div id="cmp-out" style="margin-top:14px"></div>`;
  const run = async () => {
    store('compare', saved);
    const q = k => { const [start, end] = range(saved[k]); return { start, end, tag: saved[k].tag, side: saved[k].side }; };
    const [a, b] = await Promise.all([api('summary', q('a')), api('summary', q('b'))]);
    const rows = [['Net P&L', 'net_pnl', money], ['Trades', 'trades', x => x], ['Win rate', 'win_rate', pct], ['Profit factor', 'profit_factor', ratio],
      ['Per trade', 'expectancy', money], ['Avg win', 'avg_win', money], ['Avg loss', 'avg_loss', money], ['Fees', 'fees', money], ['Max drawdown', 'max_drawdown', money]];
    $('#cmp-out', box).innerHTML = `<table class="t"><thead><tr><th></th><th class="num">A</th><th class="num">B</th><th class="num">A − B</th></tr></thead><tbody>
      ${rows.map(([l, k, f]) => { const x = a.summary[k], y = b.summary[k]; const diff = x != null && y != null ? x - y : null;
        return `<tr><td>${l}</td><td class="num">${f(x)}</td><td class="num">${f(y)}</td><td class="num ${k === 'fees' || k === 'max_drawdown' ? '' : pnlClass(diff)}">${diff == null ? '—' : k === 'win_rate' ? `${diff > 0 ? '+' : diff < 0 ? '−' : ''}${Math.abs(diff).toFixed(1)} pts` : (diff > 0 ? '+' : '') + f(diff)}</td></tr>`; }).join('')}
      </tbody></table>`;
  };
  $$('[data-s]', box).forEach(i => i.onchange = () => {
    saved[i.dataset.s][i.dataset.f] = i.value || undefined;
    $(`[data-custom="${i.dataset.s}"]`, box).hidden = saved[i.dataset.s].preset !== 'custom';
    run();
  });
  run();
}

// ---------------------------------------------------------------- weekly review
async function viewWeekly(seq, week) {
  crumb('Weekly review');
  if (needsSetup()) return;
  week = monday(week || iso(new Date()));
  const [d, list] = await Promise.all([api('week', { week }), api('weeks')]);
  if (seq !== renderSeq) return;
  const s = d.summary, p = d.previous;
  const delta = (x, y, f, better = 1) => {
    if (x == null || y == null || !p.trades) return '<div class="delta faint">no prior week</div>';
    const diff = x - y;
    const cls = Math.abs(diff) < 1e-9 || !better ? 'faint' : diff * better > 0 ? 'pos' : 'neg';
    return `<div class="delta ${cls}">${diff > 0 ? '▲' : diff < 0 ? '▼' : '–'} ${f(Math.abs(diff))} vs last week</div>`;
  };
  const card = (k, v, dl) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div>${dl}</div>`;
  const isThis = week === monday(iso(new Date()));
  const trades = (items, empty) => items.length ? `<table class="t"><tbody>${items.map(t => `<tr class="click" data-id="${esc(t.id)}"><td>${mkt(t)}</td><td>${sideBadge(t.side)}</td><td class="num">${moneyHtml(t.pnl, { plus: true })}</td></tr>`).join('')}</tbody></table>` : `<div class="muted" style="padding:14px">${empty}</div>`;
  const tagList = (items, kind, empty) => items.length ? items.map(x => `<div class="row between" style="padding:6px 0;border-bottom:1px solid var(--line)"><span class="chip ${kind}">${esc(x.name)}</span><span class="muted">${x.count}× · ${moneyHtml(x.pnl)}</span></div>`).join('') : `<div class="muted">${empty}</div>`;
  const r = d.review;
  view().innerHTML = `
    <div class="grid" style="grid-template-columns: minmax(0,1fr) 260px">
      <div>
        <div class="row between">
          <div class="row"><button class="btn" id="prev">‹</button>
            <div><h1>Week of ${shortDay(d.week)}</h1><div class="muted">${longDay(d.week)} – ${longDay(d.end)}</div></div>
            <button class="btn" id="next" ${isThis ? 'disabled' : ''}>›</button></div>
          ${isThis ? '' : '<button class="btn ghost" id="thisweek">This week</button>'}
        </div>
        <div class="cards" style="margin-top:16px">
          <div class="card big"><div class="k">Net P&L</div><div class="v">${moneyHtml(s.net_pnl, { plus: true })}</div>${delta(s.net_pnl, p.net_pnl, money)}</div>
          ${card('Win rate', pct(s.win_rate), delta(s.win_rate, p.win_rate, v => v.toFixed(1) + ' pts'))}
          ${card('Trades', s.trades, delta(s.trades, p.trades, v => v, 0))}
          ${card('Profit factor', ratio(s.profit_factor), delta(s.profit_factor, p.profit_factor, v => v.toFixed(2)))}
          ${card('Per trade', moneyHtml(s.expectancy), delta(s.expectancy, p.expectancy, money))}
          ${card('Fees', money(s.fees), delta(s.fees, p.fees, money, -1))}
        </div>
        <div class="panel" style="margin-top:16px"><div class="hd"><h2>Day by day</h2><span class="legend">Click a day to open its journal</span></div><div class="bd"><div id="wd"></div></div></div>
        <div class="grid g2" style="margin-top:16px">
          <div class="panel"><div class="hd"><h2>Best trades</h2></div><div class="bd flush">${trades(d.best, 'No winning trades this week.')}</div></div>
          <div class="panel"><div class="hd"><h2>Worst trades</h2></div><div class="bd flush">${trades(d.worst, 'No losing trades this week.')}</div></div>
          <div class="panel"><div class="hd"><h2>Top tags</h2></div><div class="bd">${tagList(d.tags, 'tag', 'Tag your trades to see patterns here.')}</div></div>
          <div class="panel"><div class="hd"><h2>Mistakes</h2></div><div class="bd">${tagList(d.mistakes, 'mistake', 'No mistakes tagged this week.')}</div></div>
        </div>
        <div class="panel" style="margin-top:16px"><div class="hd"><h2>Your review</h2><span class="row"><span class="saved" id="rs">${r.updated ? 'Saved ' + dt(r.updated) : ''}</span><button class="btn primary small" id="save">Save review</button></span></div>
          <div class="bd">
            <div class="field"><label>What worked this week?</label><textarea class="note" id="worked" placeholder="Setups that paid, rules you followed, good decisions…">${esc(r.worked)}</textarea></div>
            <div class="field"><label>What didn't work?</label><textarea class="note" id="didnt" placeholder="Losses you could have avoided, rules you broke, patterns in your mistakes…">${esc(r.didnt)}</textarea></div>
            <div class="field" style="margin-bottom:0"><label>Focus for next week</label><textarea class="note" id="focus" placeholder="One to three concrete things to do or avoid next week…">${esc(r.focus)}</textarea></div>
          </div></div>
      </div>
      <div class="panel" style="align-self:start"><div class="hd"><h2>Past weeks</h2></div><div class="weeklist">
        ${list.weeks.map(w => `<a href="#/weekly/${w.week}" class="${w.week === d.week ? 'on' : ''}"><span>${shortDay(w.week)} ${w.reviewed ? '<span title="Reviewed" class="pos">✓</span>' : ''}</span><span class="${pnlClass(w.net_pnl)}">${w.trades ? money(w.net_pnl, { plus: true }) : '<span class="faint">—</span>'}</span></a>`).join('') || '<div class="muted" style="padding:14px">No weeks yet</div>'}
      </div></div>
    </div>`;
  wireRows(view());
  $('#prev').onclick = () => { location.hash = '#/weekly/' + addDays(week, -7); };
  $('#next').onclick = () => { location.hash = '#/weekly/' + addDays(week, 7); };
  if ($('#thisweek')) $('#thisweek').onclick = () => { location.hash = '#/weekly'; };
  chart($('#wd'), () => Charts.bars($('#wd'), d.days.map(x => ({ label: x.label, value: x.pnl, day: x.day, tip: `${longDay(x.day)}<br><b>${money(x.pnl, { plus: true })}</b> · ${x.trades} trades` })), { height: 200, fmt: v => money(v), onClick: it => { location.hash = '#/day/' + it.day; } }));
  const save = async () => {
    await post('week/review', { week, worked: $('#worked').value, didnt: $('#didnt').value, focus: $('#focus').value });
    $('#rs').textContent = 'Saved ' + new Date().toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  };
  $('#save').onclick = () => save().then(() => toast('Review saved')).catch(e => toast(e.message));
  ['worked', 'didnt', 'focus'].forEach(id => autosave($('#' + id), $('#rs'), save));
}

// ---------------------------------------------------------------- settings
async function viewSettings(seq) {
  crumb('Settings');
  TAGS = (await api('tags')).tags;
  if (seq !== renderSeq) return;
  const s = STATUS;
  const source = s.kalshi ? 'Your API key is saved.' : 'Enter your Kalshi API key to connect.';
  view().innerHTML = `
    <div class="grid g2">
      <div class="panel"><div class="hd"><h2>Kalshi</h2>${s.kalshi ? '<span class="badge win">CONNECTED</span>' : '<span class="badge">NOT CONNECTED</span>'}</div>
        <div class="bd">
          <p class="muted" style="margin-top:0">${source} The journal only reads your history. It never places or cancels orders. Keys are encrypted with your Windows login and stay on this PC.</p>
          <details ${s.kalshi || s.polymarket ? '' : 'open'}><summary style="cursor:pointer;font-weight:600;margin-bottom:10px">${s.kalshi ? 'Use a different API key' : 'Add your API key'}</summary>
            <p class="muted">Create a key at kalshi.com → Account & security → API keys. Kalshi gives you a key ID and a private key file to download.</p>
            <div class="field"><label>API key ID</label><input id="kid" autocomplete="off" spellcheck="false" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"></div>
            <div class="field"><label>Private key</label>
              <div class="row" style="margin-bottom:6px"><label class="btn small" style="cursor:pointer">Choose key file…<input type="file" id="kfile" accept=".pem,.key,.txt" hidden></label><span class="muted" id="kname"></span></div>
              <textarea id="kpem" spellcheck="false" placeholder="-----BEGIN RSA PRIVATE KEY-----&#10;…or choose the file above"></textarea></div>
            <div class="row"><button class="btn primary" id="ksave">Save and test</button>${s.kalshi ? '<button class="btn danger" id="kforget">Remove saved key</button>' : ''}<span class="muted" id="kmsg"></span></div>
          </details>
        </div></div>
      <div class="panel"><div class="hd"><h2>Polymarket</h2>${s.polymarket ? '<span class="badge win">CONNECTED</span>' : '<span class="badge">NOT CONNECTED</span>'}</div>
        <div class="bd">
          <p class="muted" style="margin-top:0">${s.polymarket ? `Reading wallet <span class="mono">${esc(s.polymarket.slice(0, 6) + '…' + s.polymarket.slice(-4))}</span>.` : 'Polymarket trade history is public, so no key is needed. Just your wallet address.'}</p>
          <details ${s.kalshi || s.polymarket ? '' : 'open'}><summary style="cursor:pointer;font-weight:600;margin-bottom:10px">${s.polymarket ? 'Use a different wallet' : 'Add your wallet address'}</summary>
            <p class="muted">On polymarket.com, open your profile and copy the address under your name (starts with 0x). This is for polymarket.com accounts. Polymarket US accounts aren't supported yet.</p>
            <div class="field"><label>Wallet address</label><input id="pmaddr" autocomplete="off" spellcheck="false" placeholder="0x…" value="${esc(s.polymarket || '')}"></div>
            <div class="row"><button class="btn primary" id="pmsave">Save and test</button>${s.polymarket ? '<button class="btn danger" id="pmforget">Disconnect</button>' : ''}<span class="muted" id="pmmsg"></span></div>
          </details>
        </div></div>
      <div class="panel"><div class="hd"><h2>Data</h2></div><div class="bd">
        <p class="muted" style="margin-top:0">The journal syncs when it opens and every 5 minutes while it's open. Your notes, tags and reviews are stored only on this PC, in <span class="mono">%LOCALAPPDATA%\\MarketJournal</span>.</p>
        <div class="row"><button class="btn" id="dsync" ${s.configured ? '' : 'disabled'}>Sync now</button><button class="btn" id="drebuild">Rebuild trades</button></div>
        <p class="muted">Last sync: ${s.last_sync ? dtFull(s.last_sync) : 'never'}</p>
      </div></div>
      <div class="panel"><div class="hd"><h2>Updates</h2><span class="legend">Version ${esc(s.version)}</span></div><div class="bd">
        <p class="muted" style="margin-top:0" id="umsg">${esc(s.update.running ? s.update.message : s.update.error || '')}</p>
        <div class="row"><button class="btn" id="ucheck" ${s.update.running ? 'disabled' : ''}>Check for updates</button><button class="btn primary" id="uinstall" hidden>Update now</button></div>
      </div></div>
    </div>
    <div class="panel"><div class="hd"><h2>Market names</h2><span class="legend">Rename any market. Leave a name blank to use the automatic one.</span></div><div class="bd flush">
      <table class="t"><thead><tr><th>Name shown</th><th>Platform</th><th>Series</th><th>Platform's title</th><th class="num">Trades</th><th></th></tr></thead><tbody>
      ${STATUS.series.map(x => `<tr><td><input data-series="${esc(x.ticker)}" value="${x.custom ? esc(x.name) : ''}" placeholder="${esc(x.custom ? '' : x.name)}" style="width:100%;min-width:220px;border:1px solid var(--line-2);border-radius:8px;padding:5px 9px;background:var(--panel)"></td>
        <td>${esc(x.platform)}</td><td class="mono">${esc(x.ticker.replace(/^pm:/, ''))}</td><td class="muted">${esc(x.kalshi_title)}</td><td class="num">${x.trades}</td>
        <td>${x.custom ? `<button class="btn ghost small" data-series-reset="${esc(x.ticker)}">Use automatic</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">Markets appear here after your first sync.</td></tr>'}
      </tbody></table></div></div>
    <div class="panel"><div class="hd"><h2>Tags & mistakes</h2><span class="legend">Rename or delete. Deleting removes it from every trade.</span></div><div class="bd">
      <div class="grid g2">${['tag', 'mistake'].map(kind => `<div><h3 style="margin-bottom:8px">${kind === 'tag' ? 'Tags' : 'Mistakes'}</h3>
        ${TAGS.filter(t => t.kind === kind).map(t => `<div class="row" style="padding:4px 0"><input value="${esc(t.name)}" data-rename="${t.id}" style="flex:1;border:1px solid var(--line-2);border-radius:8px;padding:5px 9px;background:var(--panel)"><span class="muted" style="width:70px">${t.uses} trade${t.uses === 1 ? '' : 's'}</span><button class="btn ghost small danger" data-del="${t.id}">Delete</button></div>`).join('') || '<div class="muted">None yet. Add them from any trade.</div>'}
        <div class="row" style="margin-top:8px"><input data-newk="${kind}" placeholder="New ${kind}…" style="flex:1;border:1px solid var(--line-2);border-radius:8px;padding:5px 9px;background:var(--panel)"><button class="btn small" data-addk="${kind}">Add</button></div></div>`).join('')}</div>
    </div></div>`;
  $('#kfile').onchange = e => {
    const f = e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => { $('#kpem').value = reader.result; $('#kname').textContent = f.name; };
    reader.readAsText(f);
  };
  $('#ksave').onclick = async () => {
    $('#kmsg').textContent = 'Testing with Kalshi…';
    try {
      await post('credentials', { key: $('#kid').value, pem: $('#kpem').value });
      $('#kpem').value = '';
      toast('Connected to Kalshi');
      await refreshStatus();
      startSync();
      viewSettings(++renderSeq);
    } catch (e) { $('#kmsg').textContent = e.message; }
  };
  if ($('#kforget')) $('#kforget').onclick = async () => {
    if (!confirm('Remove the saved API key from this PC? Your trades and notes stay. You can enter a key again any time.')) return;
    await post('credentials/forget'); await refreshStatus(); viewSettings(++renderSeq); };
  $('#pmsave').onclick = async () => {
    $('#pmmsg').textContent = 'Checking with Polymarket…';
    try {
      await post('polymarket', { address: $('#pmaddr').value });
      toast('Connected to Polymarket');
      await refreshStatus();
      startSync();
      viewSettings(++renderSeq);
    } catch (e) { $('#pmmsg').textContent = e.message; }
  };
  if ($('#pmforget')) $('#pmforget').onclick = async () => {
    if (!confirm('Disconnect Polymarket? Its trades leave the journal. Your notes on them come back if you connect the same wallet again.')) return;
    await post('polymarket/forget'); await refreshStatus(); viewSettings(++renderSeq); };
  $('#dsync').onclick = startSync;
  $('#drebuild').onclick = async () => { const r = await post('rebuild'); toast(`Rebuilt ${r.trades} trades`); };
  const renameSeries = async (ticker, name) => {
    try { await post('series/name', { ticker, name }); await refreshStatus(); toast(name ? 'Market renamed' : 'Using the automatic name'); viewSettings(++renderSeq); }
    catch (e) { toast(e.message); }
  };
  $$('[data-series]').forEach(i => i.onchange = () => renameSeries(i.dataset.series, i.value));
  $$('[data-series-reset]').forEach(b => b.onclick = () => renameSeries(b.dataset.seriesReset, ''));
  $$('[data-rename]').forEach(i => i.onchange = async () => { try { await post('tags/rename', { id: +i.dataset.rename, name: i.value }); TAGS = (await api('tags')).tags; toast('Renamed'); } catch (e) { toast(e.message); } });
  const umsg = $('#umsg'), ucheck = $('#ucheck'), uinstall = $('#uinstall');
  const watchUpdate = async () => {
    const u = (await api('status')).update;
    umsg.textContent = u.running ? u.message : u.error || '';
    if (u.running) setTimeout(watchUpdate, 700);
    else { ucheck.disabled = false; uinstall.disabled = false; }
  };
  ucheck.onclick = async () => {
    ucheck.disabled = true; uinstall.hidden = true; umsg.textContent = 'Checking GitHub…';
    try {
      const u = await api('update');
      umsg.textContent = u.available ? `Version ${u.latest} is available.${u.notes ? ' ' + u.notes : ''}` : `You have the newest version (${u.current}).`;
      uinstall.hidden = !u.available;
    } catch (e) { umsg.textContent = e.message; }
    ucheck.disabled = false;
  };
  uinstall.onclick = async () => {
    ucheck.disabled = uinstall.disabled = true;
    try { await post('update'); watchUpdate(); } catch (e) { umsg.textContent = e.message; ucheck.disabled = uinstall.disabled = false; }
  };
  if (s.update.running) watchUpdate();
  $$('[data-del]').forEach(b => b.onclick = async () => {
    const t = TAGS.find(x => x.id === +b.dataset.del);
    if (!confirm(`Delete "${t.name}"? It will be removed from ${t.uses} trade(s).`)) return;
    await post('tags/delete', { id: t.id });
    viewSettings(++renderSeq);
  });
  const addk = async kind => {
    const i = $(`[data-newk="${kind}"]`);
    if (!i.value.trim()) return;
    try { await post('tags', { name: i.value, kind }); viewSettings(++renderSeq); } catch (e) { toast(e.message); }
  };
  $$('[data-addk]').forEach(b => b.onclick = () => addk(b.dataset.addk));
  $$('[data-newk]').forEach(i => i.onkeydown = e => { if (e.key === 'Enter') addk(i.dataset.newk); });
}

// ---------------------------------------------------------------- router
const ROUTES = { dashboard: viewDashboard, trades: viewTrades, trade: viewTrade, calendar: viewCalendar, day: viewDay, reports: viewReports, weekly: viewWeekly, settings: viewSettings };
async function route() {
  const [name, arg] = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
  const key = ROUTES[name] ? name : 'dashboard';
  const nav = { trade: 'trades', day: 'calendar' }[key] || key;
  $$('[data-nav]').forEach(a => a.classList.toggle('on', a.dataset.nav === nav));
  redraws = [];
  Charts.hideTip();
  renderStatus();
  const seq = ++renderSeq;
  try { await ROUTES[key](seq, arg); }
  catch (e) {
    if (seq === renderSeq) view().innerHTML = `<div class="panel"><div class="empty"><h2>Something went wrong</h2><p>${esc(e.message)}</p><button class="btn" onclick="route()">Try again</button></div></div>`;
  }
  if (seq === renderSeq && !location.hash.startsWith('#/trade/')) view().scrollTop = 0;
}
window.addEventListener('hashchange', route);
let resizeTimer;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => redraws.forEach(f => f()), 150); });
$('#sync-btn').onclick = startSync;
$('#donate').onclick = () => post('donate').catch(e => toast(e.message));
setInterval(() => { fetch('/api/heartbeat', { method: 'POST', headers: { 'X-Token': TOKEN } }).catch(() => {}); }, 5000);

(async () => {
  try { STATUS = await api('status'); TAGS = (await api('tags')).tags; } catch (e) { /* shown by route */ }
  refreshStatus();
  route();
})();
