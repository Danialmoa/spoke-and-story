// "Remember me": a random rider ID kept in this browser, no account. While it's on, the ride buddy keeps what the rider
// tells it and the place names of their rides on Backboard under that ID. "Forget me" deletes all of it.
const RIDER_KEY = 'spokeRider';
const REMEMBER_KEY = 'spokeRemember';
const COORDS = /^\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*$/;

function readStore(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

// Without storage (private mode) the ID only lasts while the page is open.
function writeStore(key, value) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {}
}

const mem = { id: readStore(RIDER_KEY) };

function renderMemory() {
  const on = Boolean(state.rider);
  $('#mem-switch').setAttribute('aria-checked', String(on));
  $('#mem-forget').hidden = !mem.id;
  $('#mem-details').hidden = !on;
  $('#mem-note').textContent = on
    ? 'On: I keep what you tell me about your riding and the names of the places you ride. No account, no GPS.'
    : mem.id
      ? "Off: I won't use or add to what I remember. Forget me deletes it."
      : 'Off: I forget you when you leave.';
}

async function loadMemories() {
  if (!state.rider || !$('#mem-details').open) return;
  const list = $('#mem-list');
  try {
    const { memories } = await api.memories(state.rider);
    list.innerHTML = memories.length
      ? memories.map((m) => `<li>${esc(m)}</li>`).join('')
      : '<li>Nothing yet. Tell me about your bike, or what you love and hate on a ride.</li>';
  } catch (err) {
    list.innerHTML = `<li>Couldn't load it: ${esc(err.message)}</li>`;
  }
}

function setRemember(on) {
  if (on) mem.id ||= crypto.randomUUID();
  writeStore(RIDER_KEY, mem.id);
  writeStore(REMEMBER_KEY, on ? '1' : null);
  state.rider = on ? mem.id : null;
  renderMemory();
  loadMemories();
}

async function forgetMe() {
  if (!mem.id || !confirm('Forget everything I remember about you?')) return;
  const btn = $('#mem-forget');
  btn.disabled = true;
  try {
    await api.forget(mem.id);
  } catch (err) {
    $('#mem-note').textContent = `Couldn't forget you just now: ${err.message} Try again.`;
    return;
  } finally {
    btn.disabled = false;
  }
  mem.id = null;
  writeStore(RIDER_KEY, null);
  writeStore(REMEMBER_KEY, null);
  state.rider = null;
  $('#mem-list').innerHTML = '';
  renderMemory();
}

// At the finish, or on End ride after at least 1 km. Only names leave the page: a ride from "My location" has no start.
// Simulated rides don't count: riders simulate at home before riding the route for real.
function rememberRide() {
  if (!state.rider || ride.mode === 'sim' || ride.remembered || ride.kmBase + ride.km < 1) return;
  ride.remembered = true;
  const req = state.req || readForm();
  const name = (place) => (place && !COORDS.test(place) ? place : null);
  const told = ride.stops
    .filter((s) => s.type === 'story' && ride.spoken.has(s.key))
    .map((s) => ({ place: s.name, gist: String(s.beat || s.text || '').slice(0, 150) }));
  api.rememberRide({ rider: state.rider, start: name(req.start), end: name(req.end), told: told.slice(0, 12) });
}

function wireMemory() {
  state.rider = mem.id && readStore(REMEMBER_KEY) ? mem.id : null;
  $('#mem-switch').addEventListener('click', () => setRemember(!state.rider));
  $('#mem-forget').addEventListener('click', forgetMe);
  $('#mem-details').addEventListener('toggle', loadMemories);
  renderMemory();
}
