const ride = { route: null, kms: null, steps: [], climbLeft: null, idx: 0, km: 0, offRoute: false, lastFix: null, rider: null, watch: null, timer: null, wake: null, spoken: new Set(), voice: true };
const TURN = { 0: '↰', 1: '↱', 2: '↰', 3: '↱', 4: '↖', 5: '↗', 6: '↑', 7: '⟳', 8: '⟳', 9: '↶', 10: '⚑', 11: '↑', 12: '↖', 13: '↗' };
const OFF_ROUTE_KM = 0.06;
const SIM_KM_PER_TICK = 0.06;
const RIDER = '<svg viewBox="0 0 42 42" aria-hidden="true"><circle class="rider-ring" cx="21" cy="21" r="17"/><path class="rider-tip" d="M21 8 L30 31 L21 26 L12 31 Z"/></svg>';
const fmtDist = (km) => (km >= 1 ? `${km.toFixed(1)} km` : `${Math.max(10, Math.round(km * 100) * 10)} m`);

function climbLeftFrom(coords) {
  const out = new Array(coords.length).fill(0);
  for (let i = coords.length - 2; i >= 0; i--) out[i] = out[i + 1] + Math.max(0, coords[i + 1][2] - coords[i][2]);
  return out;
}

function gradeAt(i) {
  const j = nearestIndex(ride.kms, ride.kms[i] + 0.2);
  const { coords } = ride.route;
  return j > i ? ((coords[j][2] - coords[i][2]) / ((ride.kms[j] - ride.kms[i]) * 1000)) * 100 : 0;
}

// Distance from pos to segment a-b (flat-earth approximation, fine at ride scale) and how far along it pos falls.
function toSegment(pos, a, b) {
  const kx = 111.32 * Math.cos((pos[0] * Math.PI) / 180);
  const ky = 110.57;
  const ax = (a[1] - pos[1]) * kx;
  const ay = (a[0] - pos[0]) * ky;
  const dx = (b[1] - a[1]) * kx;
  const dy = (b[0] - a[0]) * ky;
  const len2 = dx * dx + dy * dy;
  const t = len2 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2)) : 0;
  return { offKm: Math.hypot(ax + t * dx, ay + t * dy), t };
}

// Matches against segments, not points: route points can be hundreds of metres apart on straight roads.
// Only searches a window around the last match, so on a loop the finish never snaps onto the start.
function matchRoute(pos) {
  const { kms } = ride;
  const { coords } = ride.route;
  const from = nearestIndex(kms, ride.km - 0.3);
  const to = Math.min(nearestIndex(kms, ride.km + (ride.offRoute ? 10 : 1.5)) + 1, coords.length - 1);
  let best = { index: ride.idx, km: ride.km, offKm: Infinity };
  for (let i = from; i < to; i++) {
    const { offKm, t } = toSegment(pos, coords[i], coords[i + 1]);
    if (offKm < best.offKm) best = { index: i, km: kms[i] + t * (kms[i + 1] - kms[i]), offKm };
  }
  return best;
}

function pointAt(km) {
  const { coords } = ride.route;
  let i = nearestIndex(ride.kms, km);
  if (ride.kms[i] > km && i > 0) i -= 1;
  const j = Math.min(i + 1, coords.length - 1);
  const t = ride.kms[j] > ride.kms[i] ? (km - ride.kms[i]) / (ride.kms[j] - ride.kms[i]) : 0;
  return {
    lat: coords[i][0] + (coords[j][0] - coords[i][0]) * t,
    lon: coords[i][1] + (coords[j][1] - coords[i][1]) * t,
    heading: bearing(coords[i], coords[j]),
  };
}

function speak(text) {
  if (ride.voice && window.speechSynthesis) speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

function setTurn(arrow, dist, text, kind = '') {
  $('#ride-arrow').textContent = arrow;
  $('#ride-dist').textContent = dist;
  $('#ride-text').textContent = text;
  $('#ride-turn').classList.toggle('is-off', kind === 'off');
  $('#ride-turn').classList.toggle('is-done', kind === 'done');
}

function renderRide(speedKmh) {
  const { route, kms, idx } = ride;
  const total = kms[kms.length - 1];
  const done = ride.km;
  const left = Math.max(0, total - done);
  const next = ride.steps.find((s) => s.index > idx);
  if (left < 0.05) {
    setTurn('⚑', 'Done', `${total.toFixed(1)} km and ${route.climb_m} m of climbing. Now go touch some grass.`, 'done');
    if (!ride.spoken.has('done')) {
      ride.spoken.add('done');
      speak('You made it. Nice ride!');
    }
  } else if (ride.offRoute) {
    setTurn('!', 'Off route', 'Head back to the dashed line.', 'off');
  } else if (next) {
    const toNext = kms[next.index] - done;
    setTurn(TURN[next.type] || '↑', fmtDist(toNext), next.text);
    if (toNext <= 0.15 && !ride.spoken.has(next.index)) {
      ride.spoken.add(next.index);
      speak(`In ${Math.max(10, Math.round(toNext * 100) * 10)} metres, ${next.text}`);
    }
  }
  $('#r-speed').textContent = speedKmh.toFixed(1);
  $('#r-done').textContent = done.toFixed(1);
  $('#r-left').textContent = left.toFixed(1);
  $('#r-climb').textContent = Math.round(ride.climbLeft[idx]);
  $('#r-grade').textContent = String(Math.round(gradeAt(idx)) || 0);
  const eta = new Date(Date.now() + route.duration_min * (left / total) * 60000);
  $('#r-eta').textContent = eta.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

function onPosition({ lat, lon, speedKmh = null, heading = null, accuracyM = 0 }) {
  const pos = [lat, lon];
  const now = Date.now();
  const last = ride.lastFix;
  const match = matchRoute(pos);
  ride.offRoute = match.offKm > OFF_ROUTE_KM + accuracyM / 1000;
  if (!ride.offRoute) {
    ride.idx = match.index;
    ride.km = match.km;
  }
  if (speedKmh == null && last) {
    const hours = (now - last.t) / 3.6e6;
    speedKmh = hours > 0 ? haversineKm(last.pos, pos) / hours : 0;
  }
  if (heading == null) heading = last && haversineKm(last.pos, pos) > 0.005 ? bearing(last.pos, pos) : last?.heading ?? 0;
  ride.lastFix = { pos, t: now, heading };
  ride.rider.setLatLng(pos);
  ride.rider.getElement()?.style.setProperty('--h', `${heading}deg`);
  map.m.panTo(pos, { animate: true, duration: 0.35 });
  renderRide(speedKmh || 0);
}

function simulateRide() {
  const total = ride.kms[ride.kms.length - 1];
  let km = 0;
  ride.timer = setInterval(() => {
    km = Math.min(total, km + SIM_KM_PER_TICK);
    const speedKmh = Math.max(8, Math.min(45, 26 - gradeAt(nearestIndex(ride.kms, km)) * 2.2));
    onPosition({ ...pointAt(km), speedKmh });
    if (km >= total) clearInterval(ride.timer);
  }, 400);
}

async function startRide(simulate) {
  const route = currentRoute();
  if (!route || !map.m) return;
  Object.assign(ride, {
    route, kms: routeKm(route), steps: route.steps || [], climbLeft: climbLeftFrom(route.coords),
    idx: 0, km: 0, offRoute: false, lastFix: null, spoken: new Set(),
  });
  $('#ride').hidden = false;
  document.body.classList.add('riding');
  $('#ride-frame').className = `${$('#map-frame').className} ride-frame`;
  $('#ride-frame').append($('#map'));
  map.m.invalidateSize();
  ride.rider = L.marker(route.coords[0], {
    icon: L.divIcon({ className: 'rider', html: RIDER, iconSize: [0, 0] }), interactive: false, keyboard: false, zIndexOffset: 1000,
  }).addTo(map.m);
  map.m.setView(route.coords[0], 16, { animate: false });
  $('#ride-mode').textContent = simulate ? 'Simulation · fast-forward' : 'GPS';
  renderRide(0);
  try {
    ride.wake = await navigator.wakeLock?.request('screen');
  } catch {
    ride.wake = null;
  }
  if (simulate) {
    simulateRide();
  } else if (!navigator.geolocation) {
    setTurn('!', 'No GPS', 'This browser has no location. Try Simulate ride.', 'off');
  } else {
    ride.watch = navigator.geolocation.watchPosition(
      (p) => onPosition({
        lat: p.coords.latitude,
        lon: p.coords.longitude,
        speedKmh: p.coords.speed == null ? null : p.coords.speed * 3.6,
        heading: Number.isFinite(p.coords.heading) ? p.coords.heading : null,
        accuracyM: p.coords.accuracy,
      }),
      () => setTurn('!', 'No GPS', "Can't read your location. Allow location access, or try Simulate ride.", 'off'),
      { enableHighAccuracy: true, maximumAge: 1000, timeout: 15000 },
    );
  }
}

function endRide() {
  clearInterval(ride.timer);
  if (ride.watch != null) navigator.geolocation.clearWatch(ride.watch);
  ride.watch = null;
  ride.wake?.release?.();
  ride.wake = null;
  window.speechSynthesis?.cancel();
  if (ride.rider) map.m.removeLayer(ride.rider);
  $('#map-frame').prepend($('#map'));
  $('#ride').hidden = true;
  document.body.classList.remove('riding');
  map.m.invalidateSize();
  fitRoutes();
}

function wireRide() {
  $('#ride-start').addEventListener('click', () => startRide(false));
  $('#ride-sim').addEventListener('click', () => startRide(true));
  $('#ride-end').addEventListener('click', endRide);
  $('#ride-voice').addEventListener('click', (e) => {
    ride.voice = !ride.voice;
    e.currentTarget.setAttribute('aria-pressed', String(ride.voice));
    e.currentTarget.textContent = ride.voice ? 'Voice on' : 'Voice off';
    if (!ride.voice) window.speechSynthesis?.cancel();
  });
}
