const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtDur = (min) => { const m = Math.round(min); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`; };
const isNarrow = () => window.matchMedia('(max-width: 920px)').matches;
const scrollBehavior = () => (window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth');
const localDate = (iso) => new Date(`${iso}T12:00:00`);
const isoDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const weekday = (iso, style = 'long') => localDate(iso).toLocaleDateString('en-GB', { weekday: style });

function haversineKm(a, b) {
  const rad = (d) => (d * Math.PI) / 180;
  const h = Math.sin(rad(b[0] - a[0]) / 2) ** 2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(rad(b[1] - a[1]) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

function bearing(a, b) {
  const rad = (d) => (d * Math.PI) / 180;
  const y = Math.sin(rad(b[1] - a[1])) * Math.cos(rad(b[0]));
  const x = Math.cos(rad(a[0])) * Math.sin(rad(b[0])) - Math.sin(rad(a[0])) * Math.cos(rad(b[0])) * Math.cos(rad(b[1] - a[1]));
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

function cumulativeKm(coords) {
  const out = [0];
  for (let i = 1; i < coords.length; i++) out.push(out[i - 1] + haversineKm(coords[i - 1], coords[i]));
  return out;
}

const kmCache = new WeakMap();
function routeKm(route) {
  if (!kmCache.has(route)) kmCache.set(route, cumulativeKm(route.coords));
  return kmCache.get(route);
}

function nearestIndex(sorted, value) {
  let lo = 0;
  let hi = sorted.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] < value) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && value - sorted[lo - 1] < sorted[lo] - value) lo -= 1;
  return lo;
}

async function readJson(res) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join('; ') : data.detail;
    throw new Error(detail || `The server answered ${res.status}.`);
  }
  return data;
}
const getJson = async (url) => readJson(await fetch(url));
const postJson = async (url, body) => readJson(await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));

const state = {
  live: false,
  checked: false,
  bike: 'road',
  priority: 'scenic',
  result: null,
  selected: null,
  messages: [],
  thread: [{ role: 'agent', text: 'Tell me where you want to go, like "take me to Varenna along the lake" or "50 km gravel loop". I start from your location, and ask a question or two if I need to.' }],
  chatBusy: false,
  here: null,
};

const api = {
  plan: (req, delay) => (state.live ? postJson('/api/plan', req) : mockPlan(req, delay)),
  chat: (messages) => (state.live ? postJson('/api/chat', { messages, location: state.here }) : mockChat(messages)),
  pick: (req, res) => (state.live
    ? postJson('/api/pick', { request: req, routes: res.routes.map(({ coords, profile, steps, ...stats }) => stats), scenery: res.scenery })
    : mockPick(res)),
  story: (req, route) => (state.live
    ? postJson('/api/story', { request: req, route: { coords: route.coords, distance_km: route.distance_km, climb_m: route.climb_m } })
    : mockStory(route)),
  places: (q, near) => (state.live
    ? getJson(`/api/places?q=${encodeURIComponent(q)}${near ? `&near=${encodeURIComponent(near)}` : ''}`)
    : mockPlaces(q)),
  event: (body) => {
    if (state.live) postJson('/api/event', body).catch(() => {});
  },
  weekly: (place, bike, delay) => (state.live
    ? getJson(`/api/weekly?place=${encodeURIComponent(place)}&bike=${encodeURIComponent(bike)}`)
    : mockWeekly(place, bike, delay)),
};

const MY_LOC = 'My location';
// Usage events only ever carry a ~10 km area, never an exact position.
const areaOf = ([lat, lon]) => `${lat.toFixed(1)},${lon.toFixed(1)}`;

function locate() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve(null);
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (p) => resolve(`${p.coords.latitude.toFixed(5)},${p.coords.longitude.toFixed(5)}`),
      () => resolve(null),
      { timeout: 8000, maximumAge: 60000 },
    );
  });
}

async function detectApi() {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 2500);
    const res = await fetch('/api/health', { signal: ctrl.signal });
    clearTimeout(timer);
    if (!res.ok) return false;
    const data = await res.json();
    return data?.ok === true;
  } catch {
    return false;
  }
}
