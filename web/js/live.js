// The live guide: each chapter written just before its place, fun spots, area stories, speed comments and reroutes.
// Gemma writes a line in ~2 s and Kokoro needs a few seconds for its first sentence (ElevenLabs ~1 s for a line), so
// lines are asked for about a minute early.
const live = {};
const spotsCache = new WeakMap();
// Places are told inside a circle around the place itself, so they work on route, off route and after a reroute.
// Wikipedia places can sit up to 1 km off the route: their circle reaches 300 m past the planned route.
const STORY_RADIUS_KM = 0.8;
// Wide enough to catch a place from the far side of the road too.
const ROUTE_MARGIN_KM = 0.5;
const SPOT_RADIUS_KM = 0.15;
const STALE_KM = { story: 1, spot: 0.5 };
// How long Simulate crawls for a live line still being written or voiced, and how long a rider inside a place's circle
// waits for its chapter before hearing the short line instead.
const LIVE_WAIT_MS = 30000;
// A chapter is asked for ~2 km before its circle, so a slow or failed answer has minutes to be asked again: asked
// ~300 m ahead with an 8 s timeout, one slow minute on a real ride lost the place's story.
const CHAPTER_ASK_KM = 2;
const CHAPTER_TIMEOUT_MS = 30000;
const CHAPTER_TRIES = 3;
const RETRY_MS = 20000;
const COMMENT_GAP_MS = 120000;
const QUIET_NEAR_KM = 0.5;
const REROUTE_AFTER_MS = 15000;
const REROUTE_GAP_MS = 30000;
// Santa fills a quiet stretch quickly: on the first real rides 90% of the time was silence.
const AREA_QUIET_MS = 90000;
const AREA_ASK_MS = 60000;
const AREA_ASK_KM = 1;
// After an area story that failed or had nothing to say, the next one is asked sooner than a full quiet stretch.
const AREA_RETRY_MS = 30000;
const AREA_QUIET_KM = 1.5;
const AREA_STALE_KM = 2;
const round1 = (x) => Math.round(x * 10) / 10;

function resetLive() {
  Object.assign(live, {
    lines: new Map(), comment: null, top: 0, topSaid: 0, lastComment: 0, grind: 0, last: null, offSince: null, rerouting: false,
    lastReroute: 0, told: [], topics: [], entered: new Map(), asking: false, area: null, quietFrom: { t: Date.now(), km: 0 }, pos: null, odo: 0,
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
    // The server takes lines up to 1500 characters; one longer line would fail every live line after it.
    said: ride.said.map((s) => s.slice(0, 1500)),
  };
}

// '' when there's no line; the ride log says why (a timeout, no signal, or the server's answer).
function liveLine(event, extra, timeoutMs) {
  const context = { thread: ride.story?.thread || null, ...rideContext(), ...extra };
  return api.live({ event, bike: bikeOf(), context }, timeoutMs).then((r) => r.text || '', (err) => {
    rideLog(`${event} line error: ${err.message}`);
    return '';
  });
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
    const told = s.told_before || null;
    return { type: 'story', key: `story-${s.place}`, name: s.place, beat: s.beat, facts: s.facts, told, km: kmAt(s.index), lat, lon, radius };
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

// The chapter is the place's story, so a failed one is asked for again, RETRY_MS apart, while the place isn't told.
// Until the last try fails, text stays null: the rider entering the circle still waits for it (LIVE_WAIT_MS).
function askLive(stop) {
  let line = live.lines.get(stop.key);
  if (line && (line.asking || line.text != null || Date.now() < line.retryAt)) return;
  if (!line) {
    line = { text: null, waitFrom: null, tries: 0, asking: false, retryAt: 0 };
    live.lines.set(stop.key, line);
  }
  const story = stop.type === 'story';
  const context = story
    ? { place: stop.name, beat: stop.beat || null, facts: stop.facts || null, told_before: stop.told }
    : { place: stop.name, base: stop.note };
  line.asking = true;
  line.tries += 1;
  rideLog(`asked live line${line.tries > 1 ? ` (try ${line.tries})` : ''}: ${stop.name}`);
  liveLine(story ? 'chapter' : 'spot', context, story ? CHAPTER_TIMEOUT_MS : undefined).then((text) => {
    line.asking = false;
    if (!text && story && line.tries < CHAPTER_TRIES && !ride.spoken.has(stop.key)) {
      line.retryAt = Date.now() + RETRY_MS;
      rideLog(`live line failed, asking again in ${RETRY_MS / 1000} s: ${stop.name}`);
      return;
    }
    line.text = text;
    rideLog(`live line ${text ? 'written' : 'failed'}: ${stop.name}`);
    if (!ride.spoken.has(stop.key)) prepare(text, { first: true });
  });
}

// The live line only once Santa has voiced it (its first sentence), so a place is never told twice.
function readyLine(stop) {
  const text = live.lines.get(stop.key)?.text;
  return text && audioReady(text) ? text : null;
}

// When a chapter couldn't be written or voiced in time, so no place goes silent. /api/story leaves out places with
// neither a beat nor facts.
const shortLine = (stop) => `We're passing ${stop.name}: ${stop.beat || sentences(stop.facts)[0]}`;

// A chapter waits up to LIVE_WAIT_MS inside the circle for its live line, then the short line is said instead.
// A spot is told only if its line was voiced in time.
function tell(stop) {
  const line = live.lines.get(stop.key);
  const text = readyLine(stop);
  if (stop.type === 'spot' && !text) {
    if (line?.text === '') ride.spoken.add(stop.key);
    return;
  }
  if (!text && ride.voice && pending(line) && Date.now() - live.entered.get(stop.key) < LIVE_WAIT_MS) return;
  ride.spoken.add(stop.key);
  live.told.push(stop.name);
  stop.said = text || shortLine(stop);
  rideLog(`queued ${text ? 'live' : 'short line'}: ${stop.name}`);
  say(stop.said, { title: stop.name, stale: farFrom([stop.lat, stop.lon], stop.radius + STALE_KM[stop.type]) });
}

// After a voice switch: the lines already written for places just ahead, in the new voice.
function voiceLiveLines() {
  for (const stop of ride.stops) {
    const text = live.lines.get(stop.key)?.text;
    if (text && due(stop) && live.pos && kmTo(live.pos, stop) < stop.radius + leadKm()) prepare(text, { first: true });
  }
}

// Chapters are asked well ahead (CHAPTER_ASK_KM); spots, short and cheap to miss, just ahead.
const askKm = (stop) => (stop.type === 'story' ? Math.max(CHAPTER_ASK_KM, leadKm()) : leadKm());

function narrateStops(pos) {
  const near = ride.stops.filter(due).map((stop) => ({ stop, km: kmTo(pos, stop) }))
    .filter(({ stop, km }) => km < stop.radius + askKm(stop))
    .sort((a, b) => a.km - b.km);
  for (const { stop, km } of near) {
    askLive(stop);
    if (km < stop.radius && !live.entered.has(stop.key)) {
      live.entered.set(stop.key, Date.now());
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
// It's asked AREA_ASK_MS into the quiet and said at AREA_QUIET_MS, so it's written and voiced by then.
const quietPast = (ms, km, now) => (ride.mode === 'sim' ? live.odo - live.quietFrom.km >= km : now - live.quietFrom.t >= ms);

function watchArea(pos, now) {
  // A plot-twist line doesn't end the quiet: a rider on a way of their own would otherwise never get an area story.
  // Turns don't end the quiet either: in town they come every minute or two, and kept area stories from ever starting.
  if (voice.busy && !voice.item?.urgent && voice.item?.title !== 'New route') live.quietFrom = { t: now, km: live.odo };
  const placeNear = ride.stops.some((s) => due(s) && kmTo(pos, s) < s.radius + leadKm());
  const next = live.area;
  if (next?.stale()) {
    rideLog(`area story dropped, too far: ${next.title}`);
    live.area = null;
    return;
  }
  if (next) {
    // Not voiced well after the quiet ended (no signal): queued anyway, the voice tries again on its own.
    const ready = audioReady(next.text) || now - next.at > AREA_QUIET_MS;
    if (!ready || voice.busy || placeNear || !quietPast(AREA_QUIET_MS, AREA_QUIET_KM, now)) return;
    live.area = null;
    live.quietFrom = { t: now, km: live.odo };
    say(next.text, { title: next.title, stale: next.stale });
    return;
  }
  if (live.asking || !ride.voice || placeNear || !quietPast(AREA_ASK_MS, AREA_ASK_KM, now)) return;
  live.asking = true;
  rideLog('area story asked');
  const { planned } = ride;
  const point = pos.map((x) => +x.toFixed(3));
  const stale = () => ride.planned !== planned || farFrom(point, AREA_STALE_KM)();
  // Places still ahead count as told too, so an area story never takes a chapter's place.
  const thisRide = [...live.told, ...ride.stops.filter((s) => !ride.spoken.has(s.key)).map((s) => s.name)].slice(-40);
  const told = [...new Set([...thisRide, ...earlierTopics()])].slice(0, 60);
  const body = { point, bike: bikeOf(), thread: ride.story?.thread || null, told, context: rideContext() };
  api.area(body).then((r) => {
    const heard = [r.place, r.topic].filter(Boolean);
    live.told.push(...heard);
    live.topics.push(...heard);
    rideLog(r.text ? `area story written: ${r.place}` : 'area story: nothing to say');
    return { text: r.text || '', title: r.place || 'Around here' };
  }, (err) => {
    rideLog(`area story error: ${err.message}`);
    return { text: '' };
  }).then(({ text, title }) => {
    live.asking = false;
    if (ride.planned !== planned || $('#ride').hidden) return;
    if (!text) {
      // The quiet goes on: try again in AREA_RETRY_MS instead of a whole quiet stretch.
      live.quietFrom = { t: Date.now() - AREA_ASK_MS + AREA_RETRY_MS, km: live.odo - AREA_ASK_KM / 2 };
      return;
    }
    live.area = { text, title, stale, at: Date.now() };
    prepare(text, { first: true });
  });
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
  let why = 'no route';
  const fresh = await api.reroute(body).catch((err) => {
    why = err.message;
    return null;
  });
  live.rerouting = false;
  rideLog(fresh?.coords?.length ? `new route: ${fresh.distance_km} km` : `reroute failed: ${why}`);
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
