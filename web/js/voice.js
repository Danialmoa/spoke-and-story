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
  ready: 'Voice: Santa (Kokoro, running on this device)',
  failed: "Kokoro couldn't load here, so you'll hear your browser's voice.",
  blocked: '🔊 Your phone blocked the sound. Tap here to turn on Santa.',
};

const VOICE = 'am_santa';

const voice = {
  status: 'idle',
  worker: null,
  ready: null,
  todo: [],
  job: null,
  cache: new Map(),
  made: new Set(),
  player: new Audio(),
  queue: [],
  busy: false,
  stop: null,
  session: 0,
  blocked: false,
};

// Lines go to the worker one at a time so live lines can jump ahead of the ride's other lines, which are made far in
// advance. The worker can't reorder them itself: it doesn't read new messages while it's generating.
function kokoro(message, first = false) {
  return new Promise((resolve, reject) => {
    enqueue({ message, first, resolve, reject });
    nextKokoro();
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

function nextKokoro() {
  if (voice.job || !voice.todo.length) return;
  voice.job = voice.todo.shift();
  voice.worker.postMessage(voice.job.message);
}

function failVoice() {
  voice.status = 'failed';
  [voice.job, ...voice.todo].forEach((job) => job?.reject(new Error('Kokoro is not available')));
  voice.job = null;
  voice.todo = [];
  renderVoiceStatus();
}

function loadVoice() {
  if (!voice.worker) {
    try {
      voice.worker = new Worker(URL.createObjectURL(new Blob([KOKORO_WORKER], { type: 'text/javascript' })), { type: 'module' });
    } catch {
      failVoice();
      return;
    }
    voice.worker.onmessage = ({ data }) => {
      const { job } = voice;
      voice.job = null;
      if (data.error) job?.reject(new Error(data.error));
      else job?.resolve(data.blob ?? true);
      nextKokoro();
    };
    voice.worker.onerror = failVoice;
    voice.status = 'loading';
    voice.ready = kokoro({}).then(() => {
      voice.status = 'ready';
      renderVoiceStatus();
    }, failVoice);
  }
  renderVoiceStatus();
}

function renderVoiceStatus() {
  const el = $('#voice-status');
  const made = voice.status === 'ready' ? ` · ${voice.made.size} lines ready` : '';
  el.textContent = voice.blocked ? VOICE_STATUS.blocked : VOICE_STATUS[voice.status] + made;
  el.classList.toggle('is-blocked', voice.blocked);
}

// Lines wait for Santa to finish loading instead of being skipped, so they can be queued as soon as the story is here.
function prepare(text, { first = false } = {}) {
  if (!text || !voice.ready || voice.status === 'failed') return null;
  if (!voice.cache.has(text)) {
    const audio = voice.ready.then(() => (voice.status === 'ready' ? kokoro({ text, voice: VOICE }, first) : null));
    voice.cache.set(text, audio.then((blob) => {
      if (!blob) return null;
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
    if (expired(item)) continue;
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
  const url = await Promise.race([prepare(item.text), sleep(item.urgent ? 8000 : 90000).then(() => null)]);
  if (session !== voice.session || !url) return;
  if (expired(item)) return;
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
