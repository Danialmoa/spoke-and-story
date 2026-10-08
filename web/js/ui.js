const ICONS = {
  sun: '<circle cx="12" cy="12" r="4.5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
  partly: '<circle cx="8" cy="8" r="3"/><path d="M8 2.5V4M2.5 8H4M4.1 4.1l1 1M11.9 4.1l-1 1"/><path d="M9 19h8.5a3.5 3.5 0 0 0 .3-7 4.8 4.8 0 0 0-9.1 1.6A2.8 2.8 0 0 0 9 19z"/>',
  cloud: '<path d="M7 18h10a4 4 0 0 0 .4-8A5.5 5.5 0 0 0 6.8 10.6 3.7 3.7 0 0 0 7 18z"/>',
  fog: '<path d="M4 9h16M6 13h12M4 17h16"/>',
  rain: '<path d="M7 15h10a4 4 0 0 0 .4-8A5.5 5.5 0 0 0 6.8 7.6 3.7 3.7 0 0 0 7 15z"/><path d="M8 18l-1 3M12 18l-1 3M16 18l-1 3"/>',
  snow: '<path d="M7 15h10a4 4 0 0 0 .4-8A5.5 5.5 0 0 0 6.8 7.6 3.7 3.7 0 0 0 7 15z"/><path d="M8 19h.01M12 21h.01M16 19h.01" stroke-width="2.6"/>',
  storm: '<path d="M7 15h10a4 4 0 0 0 .4-8A5.5 5.5 0 0 0 6.8 7.6 3.7 3.7 0 0 0 7 15z"/><path d="M12.5 15l-2 4h3l-2 4"/>',
};

function weatherIcon(code) {
  let kind = 'storm';
  if (code === 0) kind = 'sun';
  else if (code <= 2) kind = 'partly';
  else if (code === 3) kind = 'cloud';
  else if (code <= 48) kind = 'fog';
  else if (code <= 67 || (code >= 80 && code <= 82)) kind = 'rain';
  else if (code <= 77 || code === 85 || code === 86) kind = 'snow';
  return `<svg viewBox="0 0 24 24" role="img" aria-label="${kind}">${ICONS[kind]}</svg>`;
}

function renderWeek(w) {
  const ideaDates = new Set(w.ideas.map((i) => i.date));
  $('#week-title').textContent = `This week near ${w.place.name.split(',')[0]}`;
  $('#days').innerHTML = w.days.map((d) => `
    <div class="day${d.rain_mm >= 2 ? ' is-wet' : ''}${ideaDates.has(d.date) ? ' is-idea' : ''}">
      <div class="day-top"><span class="day-name">${weekday(d.date, 'short')}</span><span class="day-date">${localDate(d.date).getDate()}</span></div>
      ${weatherIcon(d.weather_code)}
      <div class="day-temp">${Math.round(d.temp_max)}° <span class="muted">/ ${Math.round(d.temp_min)}°</span></div>
      <div class="day-meta">${d.rain_mm.toFixed(1)} mm · ${d.rain_prob}%</div>
      <div class="day-meta">${Math.round(d.wind_kmh)} km/h wind</div>
    </div>`).join('');
  $('#week-summary').textContent = w.summary;
  $('#ideas').innerHTML = w.ideas.map((i) => `
    <article class="idea">
      <div class="idea-day">${localDate(i.date).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'short' })}</div>
      <h3>${esc(i.title)}</h3>
      <p>${esc(i.why)}</p>
    </article>`).join('');
}

function routeCard(r, { best = false, selected = false, compact = false, chatIndex = null } = {}) {
  const bar = (label, value, max, text, color) => `
    <div class="bar-row"><dt>${label}</dt>
      <dd><div class="track"><div class="fill" style="width:${Math.min(100, (value / (max || 1)) * 100).toFixed(0)}%;background:var(--${color})"></div></div></dd>
      <dd class="val">${text}</dd></div>`;
  const views = Math.min(r.viewpoints, 5);
  const action = chatIndex == null ? `data-show="${esc(r.id)}"` : `data-chat="${chatIndex}"`;
  return `
    <article class="rcard${selected ? ' is-sel' : ''}" data-id="${esc(r.id)}">
      <div class="rcard-head">
        <span class="rid">${esc(r.id)}</span>
        ${best ? '<span class="stamp stamp-best">Best pick</span>' : ''}
        ${r.fits ? '' : `<span class="stamp stamp-warn">${esc(r.misfit || 'Off target')}</span>`}
        <span class="score">Score ${Math.round(r.score * 100)}</span>
      </div>
      <div class="readout">
        <div><b>${r.distance_km}</b><span>km</span></div>
        <div><b>${r.climb_m}</b><span>↑ m</span></div>
        <div><b>${r.max_grade_pct}</b><span>max %</span></div>
        <div><b>${fmtDur(r.duration_min)}</b><span>time</span></div>
      </div>
      ${compact ? '' : `<dl class="bars">
        ${bar('Water', r.water_km, r.distance_km, `${r.water_km} km`, 'water')}
        ${bar('Forest', r.forest_km, r.distance_km, `${r.forest_km} km`, 'forest')}
        ${bar('Quiet roads', r.quiet_pct, 100, `${r.quiet_pct}%`, 'quiet')}
        ${bar('Paved', r.paved_pct, 100, `${r.paved_pct}%`, 'paved')}
        <div class="bar-row"><dt>Viewpoints</dt><dd class="dots">${'●'.repeat(views)}${'○'.repeat(5 - views)}</dd><dd class="val">${r.viewpoints}</dd></div>
      </dl>`}
      <button type="button" class="btn-link" ${action}>Show on map</button>
    </article>`;
}

function currentRoute() {
  return state.result?.routes.find((r) => r.id === state.selected) || null;
}

function renderCards() {
  const res = state.result;
  $('#cards').innerHTML = res.routes.map((r) => routeCard(r, { best: r.id === res.best, selected: r.id === state.selected })).join('');
}

function renderLcd(r) {
  $('#lcd-id').textContent = r ? r.id : '–';
  $('#lcd-km').textContent = r ? Number(r.distance_km).toFixed(1) : '–';
  $('#lcd-up').textContent = r ? r.climb_m : '–';
  $('#lcd-grade').textContent = r ? Number(r.max_grade_pct).toFixed(1) : '–';
  $('#lcd-time').textContent = r ? fmtDur(r.duration_min) : '–';
}

function renderProfile(route) {
  const box = $('#profile');
  const read = $('#profile-read');
  if (!route || !route.profile?.length) {
    box.innerHTML = '<p class="profile-empty">Plan a ride to see where the climbs are.</p>';
    read.textContent = '';
    return;
  }
  const W = Math.max(280, Math.round(box.clientWidth));
  const H = 170;
  const m = { l: 42, r: 10, t: 12, b: 24 };
  const kms = route.profile.map((p) => p[0]);
  const eles = route.profile.map((p) => p[1]);
  const maxKm = kms[kms.length - 1] || 1;
  const lo = Math.floor(Math.min(...eles) / 100) * 100;
  const hi = Math.max(lo + 100, Math.ceil(Math.max(...eles) / 100) * 100);
  const x = (k) => m.l + (k / maxKm) * (W - m.l - m.r);
  const y = (e) => m.t + (1 - (e - lo) / (hi - lo)) * (H - m.t - m.b);
  const yStep = hi - lo > 600 ? 200 : 100;
  const xStep = maxKm > 100 ? 20 : maxKm > 40 ? 10 : 5;
  let ticks = '';
  for (let e = lo; e <= hi; e += yStep) {
    ticks += `<line class="pf-grid" x1="${m.l}" x2="${W - m.r}" y1="${y(e)}" y2="${y(e)}"/><text class="pf-tick" x="${m.l - 6}" y="${y(e) + 3.5}" text-anchor="end">${e}</text>`;
  }
  for (let k = 0; k <= maxKm; k += xStep) {
    ticks += `<text class="pf-tick" x="${x(k)}" y="${H - 6}" text-anchor="middle">${k}</text>`;
  }
  const line = route.profile.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join('');
  const area = `${line}L${x(maxKm).toFixed(1)},${H - m.b}L${m.l},${H - m.b}Z`;
  box.innerHTML = `
    <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Elevation profile for route ${esc(route.id)}">
      <defs><pattern id="pf-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect class="pf-hatch-bg" width="6" height="6"/><line class="pf-hatch" x1="0" y1="0" x2="0" y2="6"/></pattern></defs>
      ${ticks}
      <path d="${area}" fill="url(#pf-hatch)"/>
      <path class="pf-line" d="${line}"/>
      <line class="pf-guide" id="pf-guide" x1="-20" x2="-20" y1="${m.t}" y2="${H - m.b}"/>
      <circle class="pf-dot" id="pf-dot" r="5" cx="-20" cy="-20"/>
      <rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent"/>
    </svg>`;
  const idle = `${route.distance_km} km · ↑ ${route.climb_m} m`;
  read.textContent = idle;
  const svg = box.querySelector('svg');
  const guide = $('#pf-guide');
  const dot = $('#pf-dot');
  svg.addEventListener('pointermove', (ev) => {
    const px = ev.clientX - svg.getBoundingClientRect().left;
    const k = Math.min(maxKm, Math.max(0, ((px - m.l) / (W - m.l - m.r)) * maxKm));
    const i = nearestIndex(kms, k);
    const gx = x(kms[i]).toFixed(1);
    guide.setAttribute('x1', gx);
    guide.setAttribute('x2', gx);
    dot.setAttribute('cx', gx);
    dot.setAttribute('cy', y(eles[i]).toFixed(1));
    read.textContent = `km ${kms[i].toFixed(1)} · ${Math.round(eles[i])} m`;
    showHover(route, kms[i]);
  });
  svg.addEventListener('pointerleave', () => {
    guide.setAttribute('x1', -20);
    guide.setAttribute('x2', -20);
    dot.setAttribute('cx', -20);
    read.textContent = idle;
    hideHover();
  });
}

function selectRoute(id, { fit = false } = {}) {
  state.selected = id;
  $$('#cards .rcard').forEach((el) => el.classList.toggle('is-sel', el.dataset.id === id));
  const route = currentRoute();
  renderLcd(route);
  renderProfile(route);
  drawRoutes();
  if (fit) fitRoutes();
}

function showResult(res) {
  if (!res.routes?.length) {
    showError('No routes found', 'Try a shorter distance or a different start.');
    return;
  }
  state.result = res;
  state.selected = res.best || res.routes[0].id;
  $('#map-empty').hidden = true;
  $('#ride-actions').hidden = false;
  $('#pick').hidden = false;
  showPick(res);
  renderCards();
  selectRoute(state.selected, { fit: true });
}

function showPick(res) {
  $('#pick-label').textContent = res.explanation ? `Agent's pick · Route ${res.best}` : "Agent's pick";
  $('#pick-text').textContent = res.explanation || 'Your ride buddy is comparing the routes…';
}

function showError(title, detail = '') {
  const el = $('#error');
  if (!title) {
    el.hidden = true;
    return;
  }
  el.innerHTML = `<strong>${esc(title)}</strong><p>${esc(detail)}</p>`;
  el.hidden = false;
}

function setLoading(on, text = 'Scouting routes…') {
  $('#loading').hidden = !on;
  $('#loading-text').textContent = text;
  $('#plan-btn').disabled = on;
  // The game sizes itself from its canvas, so it can only start once the overlay is visible.
  if (on) BikeGame.start($('#game'));
  else BikeGame.stop();
}

function renderMode() {
  const el = $('#mode');
  el.textContent = state.live ? 'Live' : 'Sample data';
  el.classList.toggle('is-live', state.live);
  el.title = state.live
    ? 'Connected to the Spoke & Story API.'
    : 'The API is not running, so the page shows built-in sample routes around Lecco.';
  $('#sample-note').hidden = state.live;
}

function renderThread() {
  const el = $('#thread');
  el.innerHTML = state.thread.map((msg, i) => {
    if (msg.role === 'thinking') {
      return `<div class="msg msg-agent thinking"><svg class="wheel" viewBox="0 0 48 48" aria-hidden="true"><circle class="w-tire" cx="24" cy="24" r="20"/><path class="w-spokes" d="M24 7.5V40.5M7.5 24H40.5M12.3 12.3L35.7 35.7M35.7 12.3L12.3 35.7"/><circle class="w-hub" cx="24" cy="24" r="3"/></svg><span>Thinking about routes…</span></div>`;
    }
    const user = msg.role === 'user';
    let card = '';
    if (msg.result) {
      const best = msg.result.routes.find((r) => r.id === msg.result.best) || msg.result.routes[0];
      card = routeCard(best, { best: true, compact: true, chatIndex: i });
    }
    return `<div class="msg ${user ? 'msg-user' : 'msg-agent'}${msg.error ? ' msg-error' : ''}">
      <div class="msg-who">${user ? 'You' : 'Ride buddy'}</div>
      <p>${esc(msg.text)}</p>${card}</div>`;
  }).join('');
  el.scrollTop = el.scrollHeight;
}
