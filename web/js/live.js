// The live guide: each chapter rewritten just before its place, fun spots, speed comments and reroutes.
// Gemma writes a line in ~2 s but Santa needs 5-15 s to voice it, so lines are asked for about a minute early.
const live = {};
const spotsCache = new WeakMap();
const SAY_BEFORE_KM = { story: 0.3, spot: 0.1 };
// Santa voices ~2 words a second here (a 70-word chapter takes ~30 s), so the Simulate crawl waits that long at most.
const LIVE_WAIT_MS = 30000;
const COMMENT_GAP_MS = 120000;
const QUIET_NEAR_KM = 0.5;
const REROUTE_AFTER_MS = 15000;
const REROUTE_GAP_MS = 30000;
const round1 = (x) => Math.round(x * 10) / 10;

function resetLive() {
  Object.assign(live, { lines: new Map(), comment: null, top: 0, topSaid: 0, lastComment: 0, grind: 0, last: null, offSince: null, rerouting: false, lastReroute: 0 });
}

function ensureSpots(route) {
  if (!spotsCache.has(route)) spotsCache.set(route, api.spots(route.coords).then((r) => r.spots || [], () => []));
  return spotsCache.get(route);
}

const climbDone = () => ride.climbBase + ride.climbLeft[0] - ride.climbLeft[ride.idx];

// No position in here: only place names and ride numbers leave the page.
function liveLine(event, extra) {
  const context = {
    place: null,
    beat: null,
    base: null,
    thread: ride.story?.thread || null,
    reason: null,
    speed_kmh: round1(ride.speed),
    top_speed_kmh: round1(live.top),
    km_done: round1(ride.kmBase + ride.km),
    km_left: round1(ride.kms[ride.kms.length - 1] - ride.km),
    grade_pct: Math.round(gradeAt(ride.idx)) || 0,
    climb_done_m: Math.round(climbDone()),
    local_time: new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }),
    said: ride.said,
    ...extra,
  };
  return api.live({ event, bike: state.req?.bike || state.bike, context }).then((r) => r.text || '', () => '');
}

function nearestOnRoute(pos) {
  const { coords } = ride.route;
  let best = { km: 0, offKm: Infinity };
  for (let i = 0; i < coords.length - 1; i++) {
    const { offKm, t } = toSegment(pos, coords[i], coords[i + 1]);
    if (offKm < best.offKm) best = { km: ride.kms[i] + t * (ride.kms[i + 1] - ride.kms[i]), offKm };
  }
  return best;
}

// Indexes from the backend belong to the planned route; after a reroute places are found again by position.
function placeKm(item) {
  if (ride.route === ride.planned) return ride.kms[Math.min(item.index, ride.kms.length - 1)];
  const hit = nearestOnRoute([item.lat, item.lon]);
  return hit.offKm <= 1 ? hit.km : null;
}

function setStops() {
  const { coords } = ride.planned;
  const at = (stop) => ({ ...stop, km: placeKm(stop) });
  const stories = (ride.story?.stories || []).map((s) => {
    const [lat, lon] = s.lat == null ? coords[Math.min(s.index, coords.length - 1)] : [s.lat, s.lon];
    return at({ type: 'story', key: `story-${s.place}`, name: s.place, text: s.text, beat: s.beat || null, index: s.index, lat, lon });
  }).filter((s) => s.km != null);
  const spots = ride.spots.map((s) => at({ ...s, type: 'spot', key: `spot-${s.name}` }))
    .filter((s) => s.km != null && !stories.some((t) => Math.abs(t.km - s.km) < QUIET_NEAR_KM));
  ride.stops = [...stories, ...spots].sort((a, b) => a.km - b.km);
  drawSpots(spots);
}

const sayAt = (stop) => stop.km - SAY_BEFORE_KM[stop.type];
const leadKm = () => (ride.mode === 'sim' ? 2 : Math.min(1.5, Math.max(0.3, ride.speed / 60)));

function askLive(stop) {
  if (live.lines.has(stop.key)) return;
  const line = { text: null, waitFrom: null };
  live.lines.set(stop.key, line);
  const story = stop.type === 'story';
  // The pre-written chapter goes first: it's the fallback, and live lines must never leave it unvoiced.
  if (story) prepare(stop.text, { first: true });
  liveLine(story ? 'chapter' : 'spot', { place: stop.name, beat: stop.beat, base: story ? stop.text : stop.note }).then((text) => {
    line.text = text;
    if (!ride.spoken.has(stop.key)) prepare(text, { first: true });
  });
}

// The live line only once Santa has voiced it, so a chapter is never told twice.
function readyLine(stop) {
  const text = live.lines.get(stop.key)?.text;
  return text && audioReady(text) ? text : null;
}

function narrateStops(done) {
  for (const stop of ride.stops) {
    if (ride.spoken.has(stop.key)) continue;
    const at = sayAt(stop);
    if (done >= at - leadKm() && done < stop.km) askLive(stop);
    if (stop.type === 'story') {
      if (done >= at) {
        ride.spoken.add(stop.key);
        say(readyLine(stop) || stop.text, { title: stop.name, until: stop.km + 1.5 });
      }
    } else if (done >= at) {
      const text = readyLine(stop);
      if (text) say(text, { title: stop.name, until: stop.km + 0.5 });
      if (text || done > stop.km + 0.3 || live.lines.get(stop.key)?.text === '') ride.spoken.add(stop.key);
    }
  }
}

const pending = (line) => line && line.text !== '' && !(line.text && audioReady(line.text));

// Simulate only: the rider crawls while a live line is still being written or voiced (a comment, or the line for the
// next place), so the demo hears it instead of the pre-written chapter or a stale comment.
function liveWaiting() {
  if (!ride.voice) return false;
  const stop = ride.stops.find((s) => !ride.spoken.has(s.key) && live.lines.has(s.key));
  const line = [live.comment, stop && live.lines.get(stop.key)].find(pending);
  if (!line) return false;
  line.waitFrom ??= Date.now();
  return Date.now() - line.waitFrom < LIVE_WAIT_MS;
}

// Speed and reroute lines are queued only once voiced, so they never hold up a turn; until drops them when stale.
async function sayLive(event, extra, title) {
  const { route, km } = ride;
  const line = { text: null, waitFrom: null };
  live.comment = line;
  line.text = await liveLine(event, extra);
  await prepare(line.text, { first: true });
  if (line.text && ride.route === route && !$('#ride').hidden) say(line.text, { title, until: km + 1 });
}

function canComment(now) {
  return now - live.lastComment > COMMENT_GAP_MS && !ride.offRoute && !ride.stops.some((s) => Math.abs(s.km - ride.km) < QUIET_NEAR_KM);
}

function watchSpeed(now) {
  const { speed } = ride;
  const last = live.last;
  live.last = { t: now, km: ride.km };
  if (!last) return;
  // Simulate fast-forwards, so time on a climb is counted in ride time (distance / speed), not wall time.
  const dt = ride.mode === 'sim' ? ((ride.km - last.km) / Math.max(speed, 1)) * 3600 : (now - last.t) / 1000;
  live.grind = speed < 9 && gradeAt(ride.idx) >= 6 ? live.grind + dt : 0;
  // Above 90 km/h it's a GPS jump, not a descent.
  const newTop = speed > live.top && speed < 90;
  if (newTop) live.top = speed;
  const reason = newTop && speed >= 35 && speed >= live.topSaid + 3 ? 'top_speed' : live.grind >= 20 ? 'grind' : null;
  if (!reason || !canComment(now)) return;
  live.lastComment = now;
  if (reason === 'top_speed') live.topSaid = speed;
  else live.grind = 0;
  sayLive('speed', { reason }, reason === 'top_speed' ? `${Math.round(speed)} km/h` : 'The climb');
}

function reroutePoints(pos) {
  const total = ride.kms[ride.kms.length - 1];
  const points = [pos];
  for (let km = ride.km + 1; km < total - 1 && points.length <= 20; km += 5) {
    const p = pointAt(km);
    points.push([p.lat, p.lon]);
  }
  const end = ride.route.coords[ride.route.coords.length - 1];
  points.push([end[0], end[1]]);
  return points.map(([lat, lon]) => [+lat.toFixed(5), +lon.toFixed(5)]);
}

function swapRoute(route) {
  const kms = routeKm(route);
  ride.climbBase = climbDone();
  ride.kmBase += ride.km;
  Object.assign(ride, { route, kms, steps: keyTurns(route, kms), climbLeft: climbLeftFrom(route.coords), idx: 0, km: 0, offRoute: false });
  // Turns are keyed by step index, which means nothing on the new route; places are keyed by name and stay told.
  ride.spoken = new Set([...ride.spoken].filter((key) => typeof key === 'string'));
  // Queued lines with an until are pinned to km on the old route.
  voice.queue = voice.queue.filter((item) => item.until == null);
  live.last = null;
  drawRideRoute(route);
  setStops();
}

async function reroute(pos) {
  const { route } = ride;
  live.rerouting = true;
  live.lastReroute = Date.now();
  const body = { points: reroutePoints(pos), bike: state.req?.bike || state.bike, priority: state.req?.priority || state.priority };
  const fresh = await api.reroute(body).catch(() => null);
  live.rerouting = false;
  // Back on the dashed line by now: keep the planned route. On failure the off-route banner stays.
  if (!fresh?.coords?.length || ride.route !== route || !ride.offRoute || $('#ride').hidden) return;
  swapRoute(fresh);
  sayLive('reroute', {}, 'New route');
}

function watchOffRoute(pos, now) {
  if (ride.mode !== 'gps' || !ride.offRoute) {
    live.offSince = null;
    return;
  }
  live.offSince ??= now;
  if (now - live.offSince >= REROUTE_AFTER_MS && !live.rerouting && now - live.lastReroute >= REROUTE_GAP_MS) reroute(pos);
}

function watchLive(pos) {
  const now = Date.now();
  watchSpeed(now);
  watchOffRoute(pos, now);
}

resetLive();
