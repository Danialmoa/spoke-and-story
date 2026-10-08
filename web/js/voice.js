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
const SILENT_WAV = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';
const VOICE_STATUS = {
  idle: '',
  loading: "Loading Kokoro, an open-source voice model (~90 MB, first ride only)… you'll hear the browser voice until it's ready.",
  ready: 'Voice: Santa (Kokoro, running on this device)',
  failed: "Kokoro couldn't load here, so you'll hear your browser's voice.",
};

const VOICE = 'am_santa';

const voice = {
  status: 'idle',
  worker: null,
  ready: null,
  jobs: new Map(),
  nextId: 0,
  cache: new Map(),
  player: new Audio(),
  queue: [],
  busy: false,
  stop: null,
  session: 0,
};

function kokoro(message) {
  return new Promise((resolve, reject) => {
    const id = ++voice.nextId;
    voice.jobs.set(id, { resolve, reject });
    voice.worker.postMessage({ id, ...message });
  });
}

function failVoice() {
  voice.status = 'failed';
  voice.jobs.forEach((job) => job.reject(new Error('Kokoro is not available')));
  voice.jobs.clear();
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
      const job = voice.jobs.get(data.id);
      voice.jobs.delete(data.id);
      if (data.error) job?.reject(new Error(data.error));
      else job?.resolve(data.blob ?? true);
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
  $('#voice-status').textContent = VOICE_STATUS[voice.status];
}

function prepare(text) {
  if (voice.status !== 'ready' || !text) return null;
  const key = text;
  if (!voice.cache.has(key)) {
    voice.cache.set(key, kokoro({ text, voice: VOICE }).then((blob) => URL.createObjectURL(blob), () => null));
  }
  return voice.cache.get(key);
}

// iOS only lets a page play sound after it has played something during a tap, so call this from the ride-start click.
function unlockAudio() {
  voice.player.src = SILENT_WAV;
  voice.player.play().catch(() => {});
  window.speechSynthesis?.speak(new SpeechSynthesisUtterance(''));
}

// Turns jump ahead of stories in the queue but never cut off what is already playing.
function say(text, { urgent = false, title = '', until = null } = {}) {
  if (!ride.voice || !text) return;
  const item = { text, title, until, urgent };
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
    if (item.until != null && ride.km > item.until) continue;
    showSubtitle(item.title ? item : idleSubtitle());
    await speakOne(item);
  }
  showSubtitle(idleSubtitle());
  voice.busy = false;
}

async function speakOne(item) {
  const session = voice.session;
  // Stories can wait a little for Kokoro to finish loading; turn cues can't.
  if (voice.status === 'loading' && !item.urgent) await Promise.race([voice.ready, sleep(20000)]);
  const url = await Promise.race([prepare(item.text) || Promise.resolve(null), sleep(4000).then(() => null)]);
  if (session !== voice.session) return;
  await (url ? playUrl(url) : browserSay(item.text));
}

function playUrl(url) {
  return new Promise((resolve) => {
    voice.stop = resolve;
    voice.player.onended = resolve;
    voice.player.onerror = resolve;
    voice.player.src = url;
    voice.player.play().catch(resolve);
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
    setTimeout(resolve, 2500 + text.length * 80);
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
