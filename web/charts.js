// Small SVG chart helpers. No external libraries, so the app works offline.
const Charts = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const tip = () => document.getElementById('tip');

  function svg(el, height) {
    el.innerHTML = '';
    const width = Math.max(280, el.clientWidth || 600);
    const s = document.createElementNS(NS, 'svg');
    s.setAttribute('viewBox', `0 0 ${width} ${height}`);
    s.setAttribute('width', width);
    s.setAttribute('height', height);
    s.classList.add('chart');
    el.appendChild(s);
    return [s, width];
  }

  function add(parent, tag, attrs, text) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    if (text != null) n.textContent = text;
    parent.appendChild(n);
    return n;
  }

  function niceTicks(min, max, count = 5) {
    if (min === max) { min -= 1; max += 1; }
    const span = max - min;
    const step0 = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= count) || 10 * mag;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(+v.toFixed(10));
    return ticks;
  }

  function showTip(evt, html) {
    const t = tip();
    t.innerHTML = html;
    t.hidden = false;
    const x = evt.clientX + 14, y = evt.clientY - 10;
    const w = t.offsetWidth;
    t.style.left = (x + w > window.innerWidth - 8 ? evt.clientX - w - 14 : x) + 'px';
    t.style.top = Math.max(4, y - t.offsetHeight) + 'px';
  }
  const hideTip = () => { tip().hidden = true; };

  function axisY(s, ticks, y, left, right, fmt) {
    const g = add(s, 'g', { class: 'grid' });
    for (const v of ticks) {
      add(g, 'line', { x1: left, x2: right, y1: y(v), y2: y(v) });
      add(s, 'text', { x: left - 8, y: y(v) + 4, 'text-anchor': 'end' }, fmt(v));
    }
  }

  // Line chart. pts: [{x, y, tip}] sorted by x.
  function line(el, pts, opt = {}) {
    const H = opt.height || 260, L = opt.left || 64, R = 16, T = 12, B = 26;
    const [s, W] = svg(el, H);
    if (!pts.length) { add(s, 'text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, opt.empty || 'No data yet'); return; }
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    let x0 = Math.min(...xs), x1 = Math.max(...xs);
    if (x0 === x1) { x0 -= 1; x1 += 1; }
    const ticks = niceTicks(Math.min(0, ...ys), Math.max(0, ...ys));
    const y0 = Math.min(ticks[0], ...ys), y1 = Math.max(ticks[ticks.length - 1], ...ys);
    const x = v => L + (v - x0) / (x1 - x0) * (W - L - R);
    const y = v => T + (1 - (v - y0) / (y1 - y0 || 1)) * (H - T - B);
    axisY(s, ticks, y, L, W - R, opt.yFmt || (v => v));
    if (opt.xFmt) {
      const n = Math.min(6, pts.length);
      for (let i = 0; i < n; i++) {
        const p = pts[Math.round(i * (pts.length - 1) / Math.max(1, n - 1))];
        add(s, 'text', { x: x(p.x), y: H - 6, 'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle' }, opt.xFmt(p.x));
      }
    }
    add(s, 'line', { x1: L, x2: W - R, y1: y(0), y2: y(0), class: 'zero' });
    const last = ys[ys.length - 1];
    const color = opt.color || (last >= 0 ? css('--win') : css('--loss'));
    const d = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join('');
    if (opt.area !== false) {
      add(s, 'path', { d: `${d}L${x(x1).toFixed(1)},${y(0)}L${x(x0).toFixed(1)},${y(0)}Z`, fill: color, 'fill-opacity': .12 });
    }
    add(s, 'path', { d, fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round' });
    const guide = add(s, 'line', { y1: T, y2: H - B, stroke: css('--line-2'), visibility: 'hidden' });
    const dot = add(s, 'circle', { r: 4, fill: color, visibility: 'hidden' });
    const hit = add(s, 'rect', { x: L, y: T, width: W - L - R, height: H - T - B, fill: 'transparent' });
    hit.addEventListener('mousemove', evt => {
      const box = s.getBoundingClientRect();
      const mx = (evt.clientX - box.left) * (W / box.width);
      let best = 0;
      for (let i = 1; i < pts.length; i++) if (Math.abs(x(pts[i].x) - mx) < Math.abs(x(pts[best].x) - mx)) best = i;
      const p = pts[best];
      guide.setAttribute('x1', x(p.x)); guide.setAttribute('x2', x(p.x)); guide.setAttribute('visibility', 'visible');
      dot.setAttribute('cx', x(p.x)); dot.setAttribute('cy', y(p.y)); dot.setAttribute('visibility', 'visible');
      showTip(evt, p.tip || String(p.y));
    });
    hit.addEventListener('mouseleave', () => { hideTip(); guide.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); });
    if (opt.onClick) hit.addEventListener('click', evt => {
      const box = s.getBoundingClientRect();
      const mx = (evt.clientX - box.left) * (W / box.width);
      let best = 0;
      for (let i = 1; i < pts.length; i++) if (Math.abs(x(pts[i].x) - mx) < Math.abs(x(pts[best].x) - mx)) best = i;
      opt.onClick(pts[best]);
    });
  }

  // Vertical bars colored by sign. items: [{label, value, tip}]
  function bars(el, items, opt = {}) {
    const H = opt.height || 220, L = opt.left || 64, R = 12, T = 12, B = 34;
    const [s, W] = svg(el, H);
    if (!items.length) { add(s, 'text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, opt.empty || 'No data yet'); return; }
    const vals = items.map(i => i.value);
    const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals), 4);
    const y0 = Math.min(ticks[0], ...vals), y1 = Math.max(ticks[ticks.length - 1], ...vals);
    const y = v => T + (1 - (v - y0) / (y1 - y0 || 1)) * (H - T - B);
    axisY(s, ticks, y, L, W - R, opt.fmt || (v => v));
    const slot = (W - L - R) / items.length;
    const bw = Math.max(3, Math.min(48, slot * .68));
    const every = Math.ceil(items.length / Math.max(1, Math.floor((W - L - R) / 54)));
    items.forEach((it, i) => {
      const cx = L + slot * i + slot / 2;
      const top = y(Math.max(0, it.value)), bot = y(Math.min(0, it.value));
      const r = add(s, 'rect', { x: cx - bw / 2, y: top, width: bw, height: Math.max(1, bot - top), rx: 3,
        fill: it.color || (it.value >= 0 ? css('--win') : css('--loss')), 'fill-opacity': .85 });
      r.addEventListener('mousemove', evt => showTip(evt, it.tip || `${it.label}: ${it.value}`));
      r.addEventListener('mouseleave', hideTip);
      if (opt.onClick) { r.style.cursor = 'pointer'; r.addEventListener('click', () => opt.onClick(it)); }
      if (i % every === 0) add(s, 'text', { x: cx, y: H - 14, 'text-anchor': 'middle' }, it.label);
    });
    add(s, 'line', { x1: L, x2: W - R, y1: y(0), y2: y(0), class: 'zero' });
  }

  // Two values per group side by side (e.g. implied vs actual win rate).
  function grouped(el, groups, opt = {}) {
    const H = opt.height || 220, L = 48, R = 12, T = 12, B = 34;
    const [s, W] = svg(el, H);
    if (!groups.length) { add(s, 'text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, opt.empty || 'No data yet'); return; }
    const max = opt.max || Math.max(...groups.flatMap(g => [g.a, g.b]));
    const ticks = niceTicks(0, max, 4);
    const top = Math.max(max, ticks[ticks.length - 1]);
    const y = v => T + (1 - v / top) * (H - T - B);
    axisY(s, ticks, y, L, W - R, opt.fmt || (v => v));
    const slot = (W - L - R) / groups.length;
    const bw = Math.max(4, Math.min(28, slot * .32));
    const ca = css('--faint'), cb = css('--accent');
    groups.forEach((g, i) => {
      const cx = L + slot * i + slot / 2;
      [[g.a, ca, -1], [g.b, cb, 1]].forEach(([v, c, side]) => {
        const r = add(s, 'rect', { x: side < 0 ? cx - bw - 1 : cx + 1, y: y(v), width: bw, height: Math.max(1, y(0) - y(v)), rx: 3, fill: c });
        r.addEventListener('mousemove', evt => showTip(evt, g.tip || `${g.label}`));
        r.addEventListener('mouseleave', hideTip);
      });
      add(s, 'text', { x: cx, y: H - 14, 'text-anchor': 'middle' }, g.label);
    });
  }

  // Price path for one market with the trade's fills marked.
  function price(el, pts, marks, opt = {}) {
    const H = opt.height || 300, L = 48, R = 16, T = 14, B = 26;
    const [s, W] = svg(el, H);
    if (!pts.length && !marks.length) { add(s, 'text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, 'No price history available for this market'); return; }
    const all = pts.map(p => p.t).concat(marks.map(m => m.t));
    let x0 = Math.min(...all), x1 = Math.max(...all);
    if (x0 === x1) { x0 -= 60; x1 += 60; }
    const vals = pts.map(p => p.v).concat(marks.map(m => m.v));
    let lo = Math.max(0, Math.floor((Math.min(...vals) - 5) / 10) * 10), hi = Math.min(100, Math.ceil((Math.max(...vals) + 5) / 10) * 10);
    if (hi - lo < 20) { lo = Math.max(0, lo - 10); hi = Math.min(100, hi + 10); }
    const x = v => L + (v - x0) / (x1 - x0) * (W - L - R);
    const y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    axisY(s, niceTicks(lo, hi, 5), y, L, W - R, v => v + '¢');
    const n = 5;
    for (let i = 0; i < n; i++) {
      const t = x0 + (x1 - x0) * i / (n - 1);
      add(s, 'text', { x: x(t), y: H - 6, 'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle' }, opt.xFmt ? opt.xFmt(t) : t);
    }
    if (opt.band) {
      const [a, b] = opt.band;
      add(s, 'rect', { x: x(a), y: T, width: Math.max(2, x(b) - x(a)), height: H - T - B, fill: css('--accent'), 'fill-opacity': .07 });
    }
    const color = opt.color || css('--accent');
    if (pts.length) {
      const d = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join('');
      add(s, 'path', { d, fill: 'none', stroke: color, 'stroke-width': 1.8, 'stroke-linejoin': 'round' });
    }
    const hit = add(s, 'rect', { x: L, y: T, width: W - L - R, height: H - T - B, fill: 'transparent' });
    if (pts.length) {
      const guide = add(s, 'line', { y1: T, y2: H - B, stroke: css('--line-2'), visibility: 'hidden', 'pointer-events': 'none' });
      hit.addEventListener('mousemove', evt => {
        const box = s.getBoundingClientRect();
        const mx = (evt.clientX - box.left) * (W / box.width);
        let best = 0;
        for (let i = 1; i < pts.length; i++) if (Math.abs(x(pts[i].t) - mx) < Math.abs(x(pts[best].t) - mx)) best = i;
        const p = pts[best];
        guide.setAttribute('x1', x(p.t)); guide.setAttribute('x2', x(p.t)); guide.setAttribute('visibility', 'visible');
        showTip(evt, p.tip || `${p.v.toFixed(1)}¢`);
      });
      hit.addEventListener('mouseleave', () => { hideTip(); guide.setAttribute('visibility', 'hidden'); });
    }
    for (const m of marks) {
      const cx = x(m.t), cy = y(m.v);
      const up = m.kind === 'buy';
      const c = m.kind === 'settle' ? css('--warn') : up ? css('--win') : css('--loss');
      const shape = m.kind === 'settle'
        ? add(s, 'rect', { x: cx - 5, y: cy - 5, width: 10, height: 10, transform: `rotate(45 ${cx} ${cy})`, fill: c, stroke: css('--panel'), 'stroke-width': 1.5 })
        : add(s, 'path', { d: up ? `M${cx},${cy - 7}L${cx + 7},${cy + 6}L${cx - 7},${cy + 6}Z` : `M${cx},${cy + 7}L${cx + 7},${cy - 6}L${cx - 7},${cy - 6}Z`,
          fill: c, stroke: css('--panel'), 'stroke-width': 1.5 });
      shape.addEventListener('mousemove', evt => showTip(evt, m.tip));
      shape.addEventListener('mouseleave', hideTip);
    }
  }

  return { line, bars, grouped, price, hideTip };
})();
