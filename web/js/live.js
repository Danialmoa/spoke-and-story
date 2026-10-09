// The live guide: each chapter rewritten just before its place, fun spots, area stories, speed comments and reroutes.
// Gemma writes a line in ~2 s but Kokoro needs 5-15 s to voice it (ElevenLabs ~1 s), so lines are asked for about a minute early.
const live = {};
const spotsCache = new WeakMap();
// Places are told inside a circle around the place itself, so they work on route, off route and after a reroute.
// Wikipedia places can sit up to 1 km off the route: their circle reaches 300 m past the planned route.
const STORY_RADIUS_KM = 0.8;
// Wide enough to catch a place from the far side of the road too.
const ROUTE_MARGIN_KM = 0.5;
const SPOT_RADIUS_KM = 0.15;
const STALE_KM = { story: 1, spot: 0.5 };
// Kokoro voices ~2 words a second here (a 70-word chapter takes ~30 s), so the Simulate crawl waits that long at most.
const LIVE_WAIT_MS = 30000;
const COMMENT_GAP_MS = 120000;
const QUIET_NEAR_KM = 0.5;
const REROUTE_AFTER_MS = 15000;
const REROUTE_GAP_MS = 30000;
// Santa fills a quiet stretch quickly: on the first real rides 90% of the time was silence.
const AREA_QUIET_MS = 150000;
const AREA_QUIET_KM = 1.5;
const AREA_STALE_KM = 2;
const round1 = (x) => Math.round(x * 10) / 10;

function resetLive() {
  Object.assign(live, {
    lines: new Map(), comment: null, top: 0, topSaid: 0, lastComment: 0, grind: 0, last: null, offSince: null, rerouting: false,
    lastReroute: 0, told: [], entered: new Set(), asking: false, quietFrom: { t: Date.now(), km: 0 }, pos: null, odo: 0,
  });
}

function ensureSpots(route) {
  if (!spotsCache.has(route)) spotsCache.set(route, api.spots(route.coords).then((r) => r.spots || [], () => []));
  return spotsCache.get(route);
}

const climbDone = () => ride.climbBase + ride.climbLeft[0] - ride.climbLeft[ride.idx];
const bikeOf = () => state.req?.bike || state.bike;
const kmTo = (pos, stop) => haversineKm(pos, [stop.lat, stop.lon]);
const farFrom = (place, km) => () => Boolean(ride.lastFix) && haversineKm(ride.lastFix.pos, place) > km;

// No position in here: only place names and ride numbers leave the page (/api/area adds a point rounded to ~100 m).
function rideContext() {
  return {
    speed_kmh: round1(ride.speed),
    top_speed_kmh: round1(live.top),
    km_done: round1(ride.kmBase + ride.km),
    km_left: round1(ride.kms[ride.kms.length - 1] - ride.km),
    grade_pct: Math.round(gradeAt(ride.idx)) || 0,
    climb_done_m: Math.round(climbDone()),
    local_time: new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }),
    said: ride.said,
  };
}

function liveLine(event, extra) {
  const context = { thread: ride.story?.thread || null, ...rideContext(), ...extra };
  return api.live({ event, bike: bikeOf(), context }).then((r) => r.text || '', () => '');
}

function kmToRoute(pos, coords) {
  let best = Infinity;
  for (let i = 0; i < coords.length - 1; i++) best = Math.min(best, toSegment(pos, coords[i], coords[i + 1]).offKm);
  return best;
}

// Once per ride, on the planned route. km is where the planned route passes the place: it only spaces and orders stops.
function setStops() {
  const { coords } = ride.planned;
  const kms = routeKm(ride.planned);
  const kmAt = (index) => kms[Math.min(index, kms.length - 1)];
  const stories = (ride.story?.stories || []).map((s) => {
    const [lat, lon] = s.lat == null ? coords[Math.min(s.index, coords.length - 1)] : [s.lat, s.lon];
    const radius = Math.max(STORY_RADIUS_KM, kmToRoute([lat, lon], coords) + ROUTE_MARGIN_KM);
    return { type: 'story', key: `story-${s.place}`, name: s.place, text: s.text, beat: s.beat || null, km: kmAt(s.index), lat, lon, radius };
  });
  const spots = ride.spots.map((s) => ({ ...s, type: 'spot', key: `spot-${s.name}`, km: kmAt(s.index), radius: SPOT_RADIUS_KM }))
    .filter((s) => !stories.some((t) => Math.abs(t.km - s.km) < QUIET_NEAR_KM));
  ride.stops = [...stories, ...spots].sort((a, b) => a.km - b.km);
  drawSpots(spots);
}

const leadKm = () => (ride.mode === 'sim' ? 2 : Math.min(1.5, Math.max(0.3, ride.speed / 60)));
// On a loop the last places sit near the start: they wait until half their distance is ridden, or they'd come first.
// The odometer counts every fix, so riding a way of one's own counts too.
const due = (stop) => !ride.spoken.has(stop.key) && (Boolean(state.req?.end) || live.odo >= stop.km / 2);

function askLive(stop) {
  if (live.lines.has(stop.key)) return;
  const line = { text: null, waitFrom: null };
  live.lines.set(stop.key, line);
  const story = stop.type === 'story';
  // With Kokoro the pre-written chapter is voiced first: it's the fallback, and a slow phone must never leave it unvoiced.
  if (story && voiceAhead()) prepare(stop.text, { first: true });
  rideLog(`asked live line: ${stop.name}`);
  liveLine(story ? 'chapter' : 'spot', { place: stop.name, beat: stop.beat, base: story ? stop.text : stop.note }).then((text) => {
    line.text = text;
    rideLog(`live line ${text ? 'written' : 'failed'}: ${stop.name}`);
    if (!ride.spoken.has(stop.key)) prepare(text, { first: true });
  });
}

// The live line only once Santa has voiced it, so a chapter is never told twice.
function readyLine(stop) {
  const text = live.lines.get(stop.key)?.text;
  return text && audioReady(text) ? text : null;
}

// A chapter falls back to the pre-written one; a spot is told only if its line was voiced in time.
function tell(stop) {
  const text = readyLine(stop);
  if (stop.type === 'spot' && !text) {
    if (live.lines.get(stop.key)?.text === '') ride.spoken.add(stop.key);
    return;
  }
  ride.spoken.add(stop.key);
  live.told.push(stop.name);
  rideLog(`queued ${text ? 'live' : 'pre-written'}: ${stop.name}`);
  say(text || stop.text, { title: stop.name, stale: farFrom([stop.lat, stop.lon], stop.radius + STALE_KM[stop.type]) });
}

function narrateStops(pos) {
  const near = ride.stops.filter(due).map((stop) => ({ stop, km: kmTo(pos, stop) }))
    .filter(({ stop, km }) => km < stop.radius + leadKm())
    .sort((a, b) => a.km - b.km);
  for (const { stop, km } of near) {
    askLive(stop);
    if (km < stop.radius && !live.entered.has(stop.key)) {
      live.entered.add(stop.key);
      rideLog(`entered circle: ${stop.name}`);
    }
    if (km < stop.radius) tell(stop);
  }
}

const pending = (line) => line && line.text !== '' && !(line.text && audioReady(line.text));

// Simulate only: the rider crawls while a live line is still being written or voiced (a comment, an area story or the
// line for a place ahead), so the demo hears it instead of the pre-written chapter or a stale comment.
function liveWaiting() {
  if (!ride.voice) return false;
  const lines = [live.comment, ...ride.stops.filter((s) => !ride.spoken.has(s.key)).map((s) => live.lines.get(s.key))];
  return lines.some((line) => {
    if (!pending(line)) return false;
    line.waitFrom ??= Date.now();
    return Date.now() - line.waitFrom < LIVE_WAIT_MS;
  });
}

// Comment, reroute and area lines are queued only once voiced, so they never hold up a turn.
async function sayLive(ask, stale) {
  const line = { text: null, waitFrom: null };
  live.comment = line;
  const { text, title } = await ask;
  line.text = text;
  await prepare(text, { first: true });
  if (text && !stale() && !$('#ride').hidden) say(text, { title, stale });
}

function sameRoute() {
  const { route, km } = ride;
  return () => ride.route !== route || ride.km > km + 1;
}

function canComment(now, pos) {
  return now - live.lastComment > COMMENT_GAP_MS && !ride.offRoute
    && !ride.stops.some((s) => kmTo(pos, s) < Math.max(s.radius, QUIET_NEAR_KM));
}

function watchSpeed(pos, now) {
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
  if (!reason || !canComment(now, pos)) return;
  live.lastComment = now;
  if (reason === 'top_speed') live.topSaid = speed;
  else live.grind = 0;
  const title = reason === 'top_speed' ? `${Math.round(speed)} km/h` : 'The climb';
  sayLive(liveLine('speed', { reason }).then((text) => ({ text, title })), sameRoute());
}

// After a long quiet with no place ahead, a story about wherever the rider is, e.g. on a way of their own.
function watchArea(pos, now) {
  // A plot-twist line doesn't end the quiet: a rider on a way of their own would otherwise never get an area story.
  if (voice.busy && voice.item?.title !== 'New route') live.quietFrom = { t: now, km: live.odo };
  const quiet = ride.mode === 'sim' ? live.odo - live.quietFrom.km >= AREA_QUIET_KM : now - live.quietFrom.t >= AREA_QUIET_MS;
  if (!quiet || live.asking || !ride.voice || ride.stops.some((s) => due(s) && kmTo(pos, s) < s.radius + leadKm())) return;
  live.quietFrom = { t: now, km: live.odo };
  live.asking = true;
  rideLog('area story asked');
  const { planned } = ride;
  const point = pos.map((x) => +x.toFixed(3));
  // Places still ahead count as told too, so an area story never takes a chapter's place.
  const told = [...live.told, ...ride.stops.filter((s) => !ride.spoken.has(s.key)).map((s) => s.name)].slice(-40);
  const body = { point, bike: bikeOf(), thread: ride.story?.thread || null, told, context: rideContext() };
  const ask = api.area(body).then((r) => {
    if (r.place) live.told.push(r.place);
    rideLog(r.text ? `area story written: ${r.place}` : 'area story: nothing to say');
    return { text: r.text || '', title: r.place || 'Around here' };
  }, () => ({ text: '' })).finally(() => { live.asking = false; });
  sayLive(ask, () => ride.planned !== planned || farFrom(point, AREA_STALE_KM)());
}

// A to B: straight to the destination, so a rider who chose their own way isn't pulled back onto the planned one.
// Loops: through the rest of the route, or the new route would just head home.
function reroutePoints(pos) {
  const points = [pos];
  if (!state.req?.end) {
    const total = ride.kms[ride.kms.length - 1];
    for (let km = ride.km + 1; km < total - 1 && points.length <= 20; km += 5) {
      const p = pointAt(km);
      points.push([p.lat, p.lon]);
    }
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
  // Turn lines are pinned to km on the old route; place lines are pinned to the place and stay queued.
  voice.queue = voice.queue.filter((item) => item.until == null);
  live.last = null;
  drawRideRoute(route);
}

async function reroute(pos) {
  const { route } = ride;
  live.rerouting = true;
  live.lastReroute = Date.now();
  rideLog('rerouting…');
  const body = { points: reroutePoints(pos), bike: bikeOf(), priority: state.req?.priority || state.priority };
  const fresh = await api.reroute(body).catch(() => null);
  live.rerouting = false;
  rideLog(fresh?.coords?.length ? `new route: ${fresh.distance_km} km` : 'reroute failed');
  // Back on the dashed line by now: keep the planned route. On failure the off-route banner stays.
  if (!fresh?.coords?.length || ride.route !== route || !ride.offRoute || $('#ride').hidden) return;
  swapRoute(fresh);
  sayLive(liveLine('reroute').then((text) => ({ text, title: 'New route' })), sameRoute());
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
  if (live.pos) live.odo += haversineKm(live.pos, pos);
  live.pos = pos;
  if (!ride.spoken.has('finish')) {
    narrateStops(pos);
    watchArea(pos, now);
  }
  watchSpeed(pos, now);
  watchOffRoute(pos, now);
}

resetLive();
