// Kokoro (open-weight TTS, Apache 2.0) runs in a worker so generating speech never freezes the map.
// The worker handles one sentence at a time: the ONNX session can't run two generations at once.
const KOKORO_WORKER = `
import { KokoroTTS } from 'https://cdn.jsdelivr.net/npm/kokoro-js@1.2.1/+esm';
const loading = KokoroTTS.from_pretrained('onnx-community/Kokoro-82M-v1.0-ONNX', { dtype: 'q8', device: 'wasm' });
let chain = Promise.resolve();
async function handle({ id, text, voice }) {
  try {
    const tts = await loading;
    if (!text) {
      self.postMessage({ id, ready: true });
      return;
    }
    const audio = await tts.generate(text, { voice });
    self.postMessage({ id, blob: audio.toBlob() });
  } catch (err) {
    self.postMessage({ id, error: String(err) });
  }
}
self.onmessage = ({ data }) => {
  chain = chain.then(() => handle(data));
};
`;
// 0.1 s of real silence: iOS ignored an empty clip, so the tap never unlocked sound and Santa stayed silent.
const SILENT_WAV = 'data:audio/wav;base64,UklGRkQDAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YSADAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgA==';
const VOICE_STATUS = {
  idle: '',
  loading: 'Loading Santa, an open-source voice model (~115 MB, first ride only)…',
  failed: "Kokoro couldn't load here, so you'll hear your browser's voice.",
  blocked: '🔊 Your phone blocked the sound. Tap here to turn on Santa.',
};
const ENGINES = { eleven: 'ElevenLabs (fast)', kokoro: 'Kokoro (open source, on this device)' };
const ENGINE_KEY = 'spoke-voice';
// A few ElevenLabs requests at a time keeps the server and the account's concurrency limit happy.
const ELEVEN_AT_ONCE = 3;

const VOICE = 'am_santa';

const voice = {
  status: 'idle',
  engine: null,
  elevenOk: null,
  note: '',
  offline: false,
  worker: null,
  ready: null,
  todo: [],
  job: null,
  fetching: 0,
  fails: 0,
  cache: new Map(),
  made: new Set(),
  player: new Audio(),
  queue: [],
  busy: false,
  stop: null,
  session: 0,
  blocked: false,
};

function wantedEngine() {
  try {
    return localStorage.getItem(ENGINE_KEY) === 'kokoro' ? 'kokoro' : 'eleven';
  } catch {
    return 'eleven';
  }
}

// Kokoro takes seconds per line, so it starts on a route's lines as soon as they're written. ElevenLabs costs per
// character and needs ~1 s, so it waits for the ride to start: riders often look at several routes first.
const voiceAhead = () => voice.engine === 'kokoro';

// Lines wait in one queue whatever the engine, so live lines can jump ahead of the ride's other lines (made far in
// advance) and lines still waiting simply go to Kokoro after a fallback.
function makeAudio(text, first = false) {
  return new Promise((resolve, reject) => {
    enqueue({ message: { text, voice: VOICE }, first, resolve, reject });
    nextLine();
  });
}

function enqueue(job) {
  const at = job.first ? voice.todo.findIndex((j) => !j.first) : -1;
  voice.todo.splice(at < 0 ? voice.todo.length : at, 0, job);
}

// A line still waiting its turn moves into the fast lane, e.g. the next turn while live lines are being made.
function hurry(text) {
  const i = voice.todo.findIndex((job) => !job.first && job.message.text === text);
  if (i < 0) return;
  const [job] = voice.todo.splice(i, 1);
  enqueue({ ...job, first: true });
}

// Kokoro's worker gets one line at a time: it can't run two generations at once, nor reorder what it was sent.
function nextLine() {
  if (voice.engine === 'eleven') {
    while (voice.fetching < ELEVEN_AT_ONCE && voice.todo.length) fetchLine(voice.todo.shift());
  } else if (!voice.job && voice.todo.length) {
    voice.job = voice.todo.shift();
    voice.worker.postMessage(voice.job.message);
  }
}

async function fetchLine(job) {
  voice.fetching += 1;
  try {
    const res = await fetch('/api/tts', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: job.message.text }), signal: AbortSignal.timeout(20000),
    });
    if (res.ok) {
      job.resolve(await res.blob());
      voice.offline = false;
      voice.fails = 0;
    } else {
      const { detail } = await res.json().catch(() => ({}));
      const reason = typeof detail === 'string' ? detail : `The voice server answered ${res.status}.`;
      // Switching to Kokoro means a 115 MB download, maybe on mobile data: only when ElevenLabs is really out (no key,
      // budget spent, or failing again and again). A single bad line or hiccup just skips that line.
      if (res.status === 429 || res.status === 503 || (res.status !== 422 && ++voice.fails >= 3)) {
        fallBack(reason);
        if (voice.status === 'failed') job.resolve(null);
        else enqueue(job);
      } else {
        rideLog(`voice skipped one line: ${reason}`);
        job.resolve(null);
      }
    }
  } catch {
    // No signal, e.g. in a tunnel: Kokoro's download would fail too. The line is asked for again when it's needed.
    if (!voice.offline) rideLog('ElevenLabs out of reach (no signal?), lines are tried again when needed');
    voice.offline = true;
    if (voice.engine === 'eleven') voice.cache.delete(job.message.text);
    job.resolve(null);
  } finally {
    voice.fetching -= 1;
    nextLine();
  }
}

function fallBack(reason) {
  if (voice.engine !== 'eleven') return;
  rideLog(`${reason} Switching to Kokoro.`);
  voice.note = 'ElevenLabs unavailable, switched to Kokoro. ';
  startKokoro();
  // Kokoro is slow on phones, so the rest of the ride is voiced ahead again, as when the rider picks it.
  if (ride.story && !$('#ride').hidden) prepareRide(ride.planned, ride.story, ride.km);
}

function failVoice() {
  // Kokoro failing to load after the rider switched back to ElevenLabs changes nothing.
  if (voice.engine !== 'kokoro') return;
  voice.status = 'failed';
  [voice.job, ...voice.todo].forEach((job) => job?.reject(new Error('Kokoro is not available')));
  voice.job = null;
  voice.todo = [];
  renderVoiceStatus();
}

function useEleven() {
  voice.engine = 'eleven';
  voice.status = 'ready';
  voice.ready = Promise.resolve();
  renderVoiceStatus();
  nextLine();
}

// Kokoro (~115 MB) is downloaded only when it's chosen or needed as the fallback.
function startKokoro() {
  voice.engine = 'kokoro';
  if (!voice.worker) {
    try {
      voice.worker = new Worker(URL.createObjectURL(new Blob([KOKORO_WORKER], { type: 'text/javascript' })), { type: 'module' });
    } catch {
      failVoice();
      voice.ready = Promise.resolve();
      return voice.ready;
    }
    voice.worker.onmessage = ({ data }) => {
      const { job } = voice;
      voice.job = null;
      if (data.error) job?.reject(new Error(data.error));
      else job?.resolve(data.blob ?? true);
      nextLine();
    };
    voice.worker.onerror = failVoice;
  }
  voice.status = 'loading';
  // The empty warm-up line goes ahead of every waiting line: it resolves once the model has loaded.
  voice.ready = new Promise((resolve, reject) => {
    voice.todo.unshift({ message: {}, first: true, resolve, reject });
    nextLine();
  }).then(() => {
    voice.status = 'ready';
    renderVoiceStatus();
  }, failVoice);
  renderVoiceStatus();
  return voice.ready;
}

function loadVoice() {
  if (!voice.ready) {
    voice.ready = api.voice().then((r) => r.elevenlabs === true, () => false).then((ok) => {
      voice.elevenOk = ok;
      return ok && wantedEngine() === 'eleven' ? useEleven() : startKokoro();
    });
  }
  renderVoiceStatus();
}

// The rider's pick. Lines already made are made again in the new voice, so one ride doesn't switch back and forth.
function chooseVoice(engine) {
  try {
    localStorage.setItem(ENGINE_KEY, engine);
  } catch {
    // Private mode: the pick lasts this visit.
  }
  if (engine === voice.engine) return;
  voice.todo.forEach((job) => job.resolve(null));
  voice.todo = [];
  voice.cache = new Map();
  voice.made = new Set();
  voice.note = '';
  rideLog(`voice switched to ${ENGINES[engine]}`);
  if (engine === 'kokoro') startKokoro();
  else useEleven();
  if (ride.story && !$('#ride').hidden) prepareRide(ride.planned, ride.story, ride.km);
}

function renderVoiceStatus() {
  const el = $('#voice-status');
  const text = voice.status === 'ready'
    ? `Voice: ${ENGINES[voice.engine]} · tap for the ride log · ${voice.made.size} lines ready`
    : VOICE_STATUS[voice.status];
  el.textContent = voice.blocked ? VOICE_STATUS.blocked : voice.note + text;
  el.classList.toggle('is-blocked', voice.blocked);
  $('#voice-pick').hidden = !voice.elevenOk;
  $$('#voice-pick button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === (voice.engine || wantedEngine()))));
}

// Lines wait for the voice to be ready instead of being skipped, so they can be queued as soon as the story is here.
function prepare(text, { first = false } = {}) {
  if (!text || !voice.ready || voice.status === 'failed') return null;
  if (!voice.cache.has(text)) {
    const { cache } = voice;
    // A line queued for the voice the rider switched away from must not be made (and paid for) after the switch.
    const audio = voice.ready.then(() => (voice.status === 'ready' && cache === voice.cache ? makeAudio(text, first) : null));
    cache.set(text, audio.then((blob) => {
      // Made for the voice the rider just switched away from.
      if (!blob || cache !== voice.cache) return null;
      voice.made.add(text);
      renderVoiceStatus();
      return URL.createObjectURL(blob);
    }, () => null));
  } else if (first) {
    hurry(text);
  }
  return voice.cache.get(text);
}

const audioReady = (text) => voice.status === 'failed' || voice.made.has(text);

// iOS only lets a page play sound after it has played something during a tap, so call this from the ride-start click.
function unlockAudio() {
  voice.player.src = SILENT_WAV;
  voice.player.play().catch(() => {});
  window.speechSynthesis?.speak(new SpeechSynthesisUtterance(''));
}

// until is a km on the current route (turns); stale() turns true once a line is too late, e.g. far past its place.
const expired = (item) => (item.until != null && ride.km > item.until) || Boolean(item.stale?.());

// Turns jump ahead of stories in the queue but never cut off what is already playing.
function say(text, { urgent = false, title = '', until = null, stale = null } = {}) {
  if (!ride.voice || !text) return;
  const item = { text, title, until, stale, urgent };
  if (urgent) {
    const firstStory = voice.queue.findIndex((q) => !q.urgent);
    voice.queue.splice(firstStory < 0 ? voice.queue.length : firstStory, 0, item);
  } else {
    voice.queue.push(item);
  }
  if (!voice.busy) drainVoice();
}

async function drainVoice() {
  voice.busy = true;
  while (voice.queue.length) {
    const item = voice.queue.shift();
    if (expired(item)) {
      rideLog(`dropped, too late: ${item.title || 'turn'}`);
      continue;
    }
    if (!item.urgent) ride.said = [...ride.said, item.text].slice(-3);
    showSubtitle(item.title ? item : idleSubtitle());
    voice.item = item;
    await speakOne(item);
  }
  showSubtitle(idleSubtitle());
  voice.busy = false;
  voice.item = null;
}

// One voice only: switching to the browser voice when Santa was late sounded like two people talking.
// A story waits for Santa's audio; a turn that isn't ready in time stays on the banner instead.
// The browser voice is used only when Kokoro can't run at all on this device.
async function speakOne(item) {
  const session = voice.session;
  if (voice.status === 'failed' || !voice.ready) {
    await browserSay(item.text);
    return;
  }
  const asked = Date.now();
  const url = await Promise.race([prepare(item.text), sleep(item.urgent ? 8000 : 90000).then(() => null)]);
  if (session !== voice.session) return;
  if (!url) {
    rideLog(`skipped, voice not ready after ${Math.round((Date.now() - asked) / 1000)} s: ${item.title || 'turn'}`);
    return;
  }
  if (expired(item)) {
    rideLog(`dropped, too late: ${item.title || 'turn'}`);
    return;
  }
  rideLog(`saying: ${item.title || 'turn'}`);
  await playUrl(url);
}

function playUrl(url) {
  return new Promise((resolve) => {
    voice.stop = resolve;
    voice.player.onended = resolve;
    voice.player.onerror = resolve;
    voice.player.src = url;
    voice.player.play().catch((err) => {
      if (err.name === 'NotAllowedError') {
        voice.blocked = true;
        renderVoiceStatus();
      }
      resolve();
    });
  });
}

function browserSay(text) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis) {
      resolve();
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.05;
    utterance.pitch = 1.15;
    utterance.onend = resolve;
    utterance.onerror = resolve;
    voice.stop = resolve;
    // Chrome sometimes never fires onend; one lost event must not freeze the narration.
    setTimeout(resolve, 4000 + text.length * 110);
    speechSynthesis.speak(utterance);
  });
}

function stopVoice() {
  voice.session += 1;
  voice.queue = [];
  voice.player.pause();
  window.speechSynthesis?.cancel();
  voice.stop?.();
  showSubtitle(null);
}

function idleSubtitle() {
  return ride.storyWait ? WAITING : null;
}

function showSubtitle(item) {
  $('#ride-story').hidden = !item;
  if (!item) return;
  $('#ride-story-title').textContent = item.title;
  $('#ride-story-text').textContent = item.text;
}
