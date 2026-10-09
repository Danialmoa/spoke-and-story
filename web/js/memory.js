// "Remember me": what the rider tells the ride buddy and a short log of their rides, kept in this browser only. The
// chat and story requests carry what they need; no server stores any of it. "Forget me" deletes it.
const REMEMBER_KEY = 'spokeRemember';
const PREFS_KEY = 'spokePrefs';
const RIDES_KEY = 'spokeRides';
const MAX_PREFS = 30;
const MAX_RIDES = 50;

const mem = { prefs: loadLocal(PREFS_KEY, []), rides: loadLocal(RIDES_KEY, []) };

// Coordinates (GPS, a Home set from GPS, a pin) differ a little every ride, so they all count as "my location".
// Coordinates to ~1 km: Home and pins stay distinct routes, while a GPS start from the same street still matches.
const rideLabel = (place) => (!place ? null : COORDS.test(place)
  ? place.split(',').map((x) => (+x).toFixed(2)).join(',') : place.trim().toLowerCase());

function chatMemory() {
  return state.remember ? { known: mem.prefs.slice(0, MAX_PREFS), remember: true } : { remember: false };
}

// A repeat ride (same start and end) continues the story as the next episode. Loops from the same start aren't
// repeats: they can go anywhere.
function storyMemory(req) {
  if (!state.remember) return {};
  const start = rideLabel(req.start);
  const end = rideLabel(req.end);
  const same = end ? mem.rides.filter((r) => r.start === start && r.end === end) : [];
  const last = same[0];
  return {
    history: mem.rides.flatMap((r) => r.told).slice(0, 30),
    rides_before: same.length,
    last_episode: last?.thread ? { thread: last.thread, cliffhanger: last.cliffhanger || '' } : null,
  };
}

// For area stories: what earlier rides already told.
const earlierTopics = () => (state.remember ? mem.rides.flatMap((r) => [...r.topics, ...r.told.map((t) => t.place)]) : []);

function keepFacts(facts) {
  if (!state.remember || !facts?.length) return;
  for (const fact of facts) {
    const text = String(fact).trim();
    if (text && !mem.prefs.some((p) => p.toLowerCase() === text.toLowerCase())) mem.prefs.unshift(text);
  }
  mem.prefs = mem.prefs.slice(0, MAX_PREFS);
  saveLocal(PREFS_KEY, mem.prefs);
  renderMemory();
}

function renderMemory() {
  const on = state.remember;
  const stored = mem.prefs.length > 0 || mem.rides.length > 0;
  $('#mem-switch').setAttribute('aria-checked', String(on));
  $('#mem-forget').hidden = !stored;
  $('#mem-details').hidden = !on;
  $('#mem-note').textContent = on
    ? 'On: I keep what you tell me about your riding and a short log of your rides, on this phone only.'
    : stored
      ? "Off: I won't use or add to what I remember. Forget me deletes it."
      : 'Off: I forget you when you leave.';
  const rides = mem.rides.length;
  $('#mem-list').innerHTML = [
    ...mem.prefs.map((p) => `<li>${esc(p)}</li>`),
    mem.prefs.length ? '' : '<li>Nothing yet. Tell me about your bike, or what you love and hate on a ride.</li>',
    rides ? `<li>${rides} ride${rides === 1 ? '' : 's'} with me so far</li>` : '',
  ].join('');
}

function setRemember(on) {
  state.remember = on;
  saveLocal(REMEMBER_KEY, on || null);
  renderMemory();
}

function forgetMe() {
  if (!confirm('Forget everything I remember about you?')) return;
  mem.prefs = [];
  mem.rides = [];
  [PREFS_KEY, RIDES_KEY, REMEMBER_KEY].forEach((key) => saveLocal(key, null));
  state.remember = false;
  renderMemory();
}

// At the finish, or on End ride after at least 1 km. Simulated rides don't count: riders simulate at home before
// riding the route for real.
function rememberRide() {
  if (!state.remember || ride.mode === 'sim' || ride.remembered || ride.kmBase + ride.km < 1) return;
  ride.remembered = true;
  const req = state.req || readForm();
  const told = ride.stops
    .filter((s) => s.type === 'story' && ride.spoken.has(s.key))
    .map((s) => ({ place: s.name, gist: String(s.beat || s.said || '').slice(0, 150) }));
  mem.rides.unshift({
    date: isoDate(new Date()),
    start: rideLabel(req.start),
    end: rideLabel(req.end),
    told,
    topics: live.topics,
    thread: ride.story?.thread || null,
    cliffhanger: ride.story?.cliffhanger || null,
  });
  mem.rides = mem.rides.slice(0, MAX_RIDES);
  saveLocal(RIDES_KEY, mem.rides);
  renderMemory();
}

function wireMemory() {
  state.remember = Boolean(loadLocal(REMEMBER_KEY, false));
  // The ID of the old server-side memory: nothing uses it any more.
  saveLocal('spokeRider', null);
  $('#mem-switch').addEventListener('click', () => setRemember(!state.remember));
  $('#mem-forget').addEventListener('click', forgetMe);
  renderMemory();
}
