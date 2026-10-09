// km is along the current route; kmBase and climbBase hold what was ridden before a reroute.
const ride = {
  route: null, planned: null, kms: null, steps: [], climbLeft: null, idx: 0, km: 0, kmBase: 0, climbBase: 0, speed: 0, offRoute: false,
  lastFix: null, rider: null, watch: null, timer: null, wake: null, spoken: new Set(), said: [], voice: true, story: null, spots: [], stops: [], mode: 'gps',
};
const TURN = { 0: '↰', 1: '↱', 2: '↰', 3: '↱', 4: '↖', 5: '↗', 6: '↑', 7: '⟳', 8: '⟳', 9: '↶', 10: '⚑', 11: '↑', 12: '↖', 13: '↗' };
const OFF_ROUTE_KM = 0.06;
const SIM_KM_PER_TICK = 0.06;
// While the guide is talking the simulated rider crawls, so the narration can be heard.
const SIM_KM_TALKING = 0.008;
const stories = new WeakMap();
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

function ensureStory(route) {
  ensureSpots(route);
  ensureBrief(route);
  if (!stories.has(route)) {
    loadVoice();
    const story = api.story(state.req || readForm(), route).catch(() => null);
    stories.set(route, story);
    story.then((s) => route === currentRoute() && voiceAhead() && prepare(s?.intro, { first: true }));
  }
  return stories.get(route);
}

function turnLine(step, steps = ride.steps, story = ride.story) {
  const lines = story?.turn_lines;
  if (!lines?.length) return step.text;
  const turn = step.text.charAt(0).toLowerCase() + step.text.slice(1);
  return lines[steps.indexOf(step) % lines.length].replace('{turn}', turn);
}

// Only real direction changes are announced and shown: "continue straight", "slight left", "keep right" and
// roundabouts ridden straight through were constant chatter in towns. The heading is compared 60 m before and
// after the maneuver because ORS calls a straight-through roundabout exit a turn.
const KEY_TURNS = new Set([0, 1, 2, 3, 7, 9]);
const MIN_TURN_DEG = 35;

function turnAngle(coords, kms, index) {
  const before = coords[nearestIndex(kms, kms[index] - 0.06)];
  const after = coords[nearestIndex(kms, kms[index] + 0.06)];
  const at = coords[index];
  return Math.abs(((bearing(at, after) - bearing(before, at) + 540) % 360) - 180);
}

function keyTurns(route, kms) {
  return (route.steps || []).filter((s) => KEY_TURNS.has(s.type) && turnAngle(route.coords, kms, s.index) >= MIN_TURN_DEG);
}
const WAITING = { title: 'Getting ready', text: 'Your guide is writing the stories for this route…' };
const STORY_WAIT_MS = 20000;
// Simulate rides about ten times faster, so it voices further ahead.
const VOICE_AHEAD_KM = { gps: 3, sim: 5 };

function narrate(next, done, left) {
  const { kms } = ride;
  if (left < 0.05) {
    if (!ride.spoken.has('finish')) {
      ride.spoken.add('finish');
      say(ride.story?.finish || 'You made it. Nice ride!', { title: 'Finish' });
      // The teaser the next episode recaps: it has to be heard.
      if (ride.story?.cliffhanger) say(ride.story.cliffhanger, { title: 'Next time' });
      api.event({ event: 'ride_finish', mode: ride.mode, km: ride.route.distance_km, area: areaOf(ride.route.coords[0]) });
      rememberRide();
    }
    return;
  }
  if (next) {
    const toNext = kms[next.index] - done;
    if (toNext <= 1) prepare(turnLine(next), { first: true });
    if (toNext <= 0.2 && !ride.spoken.has(next.index)) {
      ride.spoken.add(next.index);
      say(turnLine(next), { urgent: true, until: kms[next.index] + 0.03 });
    }
  }
}

// Only what's coming up is voiced: the turns and the finish within VOICE_AHEAD_KM, besides the intro at the start and
// live lines as they're written. A ride pays (ElevenLabs) or waits (Kokoro) only for what it will say.
function voiceUpcoming() {
  // The turn lines change once the story is here.
  if (ride.storyWait) return;
  const ahead = ride.km + VOICE_AHEAD_KM[ride.mode];
  for (const step of ride.steps) {
    if (ride.kms[step.index] > ride.km && ride.kms[step.index] <= ahead) prepare(turnLine(step));
  }
  if (ride.kms[ride.kms.length - 1] <= ahead) {
    prepare(ride.story?.finish);
    prepare(ride.story?.cliffhanger);
  }
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
    setTurn('⚑', 'Done', `${(ride.kmBase + total).toFixed(1)} km and ${Math.round(ride.climbBase + route.climb_m)} m of climbing. Now go touch some grass.`, 'done');
  } else if (ride.offRoute) {
    setTurn('!', 'Off route', 'Head back to the dashed line.', 'off');
  } else if (next) {
    setTurn(TURN[next.type] || '↑', fmtDist(kms[next.index] - done), next.text);
  } else {
    setTurn('⚑', fmtDist(left), 'No more turns. Straight on to the finish.');
  }
  if (!ride.offRoute) narrate(next, done, left);
  voiceUpcoming();
  $('#r-speed').textContent = speedKmh.toFixed(1);
  $('#r-done').textContent = (ride.kmBase + done).toFixed(1);
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
  const wasOff = ride.offRoute;
  ride.offRoute = match.offKm > OFF_ROUTE_KM + accuracyM / 1000;
  if (ride.offRoute !== wasOff) rideLog(ride.offRoute ? `off route (${Math.round(match.offKm * 1000)} m)` : 'back on route');
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
  ride.speed = speedKmh || 0;
  ride.rider.setLatLng(pos);
  ride.rider.getElement()?.style.setProperty('--h', `${heading}deg`);
  map.m.panTo(pos, { animate: true, duration: 0.35 });
  renderRide(ride.speed);
  watchLive(pos);
}

function simulateRide() {
  const total = ride.kms[ride.kms.length - 1];
  let km = 0;
  const started = Date.now();
  ride.timer = setInterval(() => {
    // Wait at the start while the story is being written, or the intro and first stories get skipped.
    // Capped, so a slow story never looks like a broken ride; stories that arrive later still play further on.
    const waited = Date.now() - started;
    if (ride.storyWait && waited < STORY_WAIT_MS) {
      if (!voice.busy) showSubtitle({ ...WAITING, text: `${WAITING.text} Starting in ${Math.ceil((STORY_WAIT_MS - waited) / 1000)}s.` });
      return;
    }
    km = Math.min(total, km + (voice.busy || liveWaiting() ? SIM_KM_TALKING : SIM_KM_PER_TICK));
    const speedKmh = Math.max(8, Math.min(45, 26 - gradeAt(nearestIndex(ride.kms, km)) * 2.2));
    onPosition({ ...pointAt(km), speedKmh });
    if (km >= total) clearInterval(ride.timer);
  }, 400);
}

async function startRide(simulate) {
  const route = currentRoute();
  if (!route || !map.m) return;
  unlockAudio();
  Object.assign(ride, {
    route, planned: route, kms: routeKm(route), steps: keyTurns(route, routeKm(route)), climbLeft: climbLeftFrom(route.coords),
    idx: 0, km: 0, kmBase: 0, climbBase: 0, speed: 0, offRoute: false, lastFix: null, spoken: new Set(), said: [], story: null,
    spots: [], stops: [], mode: simulate ? 'sim' : 'gps', storyWait: true, remembered: false, log: [],
  });
  // iOS offers "Undo Typing" on every shake while a search box still has focus: on a bike that's all the time.
  document.activeElement?.blur?.();
  $('#ride-log').hidden = true;
  // The dot starts where the rider is (when known), not at the route's start, until the first GPS fix.
  const here = !simulate && state.here ? state.here.split(',').map(Number) : route.coords[0];
  resetLive();
  $('#ride').hidden = false;
  document.body.classList.add('riding');
  $('#ride-frame').className = `${$('#map-frame').className} ride-frame`;
  $('#ride-frame').append($('#map'));
  map.m.invalidateSize();
  ride.rider = L.marker(here, {
    icon: L.divIcon({ className: 'rider', html: RIDER, iconSize: [0, 0] }), interactive: false, keyboard: false, zIndexOffset: 1000,
  }).addTo(map.m);
  map.m.setView(here, 16, { animate: false });
  $('#ride-mode').textContent = simulate ? 'Simulation · fast-forward' : 'GPS';
  loadVoice();
  voice.ready.then(() => rideLog(`voice: ${voice.status === 'failed' ? "the browser's" : ENGINES[voice.engine]}`));
  showSubtitle(WAITING);
  setTimeout(() => {
    if (ride.planned !== route || !ride.storyWait) return;
    ride.storyWait = false;
    if (!voice.busy) showSubtitle(null);
  }, STORY_WAIT_MS);
  ensureStory(route).then((story) => {
    if (ride.planned !== route || $('#ride').hidden) return;
    ride.story = story;
    ride.storyWait = false;
    setStops();
    if (!voice.busy) showSubtitle(null);
    if (story?.intro && ride.km < 2) say(story.intro, { title: 'Your ride' });
    sayBrief(route);
  });
  ensureSpots(route).then((spots) => {
    if (ride.planned !== route || $('#ride').hidden) return;
    ride.spots = spots;
    setStops();
  });
  api.event({ event: 'ride_start', mode: ride.mode, km: route.distance_km, area: areaOf(route.coords[0]) });
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
  stopVoice();
  if (ride.rider) map.m.removeLayer(ride.rider);
  drawSpots([]);
  drawRoutes();
  $('#map-frame').prepend($('#map'));
  $('#ride').hidden = true;
  document.body.classList.remove('riding');
  map.m.invalidateSize();
  fitRoutes();
  rememberRide();
}

// What the guide did and why, kept on the phone only: tap the voice status to see it after a ride.
function rideLog(text) {
  ride.log = [...(ride.log || []).slice(-149), `${new Date().toLocaleTimeString('en-GB')} ${text}`];
  if (!$('#ride-log').hidden) renderRideLog();
}

function renderRideLog() {
  $('#ride-log').innerHTML = (ride.log || []).map((line) => `<li>${esc(line)}</li>`).join('');
}

function wireRide() {
  $('#ride-start').addEventListener('click', () => startRide(false));
  $('#ride-sim').addEventListener('click', () => startRide(true));
  $('#ride-end').addEventListener('click', endRide);
  $('#ride-story-min').addEventListener('click', (e) => {
    const min = $('#ride-story').classList.toggle('is-min');
    e.currentTarget.textContent = min ? '+' : '–';
    e.currentTarget.setAttribute('aria-expanded', String(!min));
    e.currentTarget.setAttribute('aria-label', min ? 'Show subtitles' : 'Hide subtitles');
  });
  $('#voice-status').addEventListener('click', () => {
    if (!voice.blocked) {
      $('#ride-log').hidden = !$('#ride-log').hidden;
      renderRideLog();
      return;
    }
    voice.blocked = false;
    unlockAudio();
    renderVoiceStatus();
  });
  $('#ride-voice').addEventListener('click', (e) => {
    ride.voice = !ride.voice;
    e.currentTarget.setAttribute('aria-pressed', String(ride.voice));
    e.currentTarget.textContent = ride.voice ? 'Voice on' : 'Voice off';
    if (!ride.voice) stopVoice();
  });
}
