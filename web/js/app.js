function setBike(v) {
  state.bike = v;
  $$('#f-bike button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === v)));
}

function setPriority(v) {
  state.priority = v;
  $$('#f-priority button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === v)));
}

function readForm() {
  const start = $('#f-start').value.trim();
  const end = $('#f-end').value.trim();
  return {
    start: start === MY_LOC && state.here ? state.here : start,
    end: end || null,
    bike: state.bike,
    priority: state.priority,
  };
}

function fillForm(req) {
  if (req.start) $('#f-start').value = req.start === state.here ? MY_LOC : req.start;
  $('#f-end').value = req.end || '';
  if (req.priority) setPriority(req.priority);
  if (req.bike) setBike(req.bike);
}

async function planFromForm() {
  showError(null);
  setLoading(true);
  if (isNarrow()) $('#map-frame').scrollIntoView({ behavior: scrollBehavior(), block: 'center' });
  try {
    showResult(await api.plan(readForm()));
  } catch (err) {
    showError("Couldn't plan that ride", err.message);
  } finally {
    setLoading(false);
  }
}

async function sendChat(raw) {
  const text = raw.trim();
  if (!text || state.chatBusy) return;
  state.chatBusy = true;
  state.messages.push({ role: 'user', content: text });
  state.thread.push({ role: 'user', text }, { role: 'thinking' });
  renderThread();
  try {
    const res = await api.chat(state.messages);
    state.thread.pop();
    state.messages.push({ role: 'assistant', content: res.text });
    const msg = { role: 'agent', text: res.text };
    if (res.type === 'plan' && res.result) {
      msg.result = res.result;
      fillForm(res.request || {});
      showResult(res.result);
    }
    state.thread.push(msg);
  } catch (err) {
    state.thread.pop();
    state.messages.pop();
    state.thread.push({ role: 'agent', text: `Something went wrong: ${err.message} Try sending it again.`, error: true });
  } finally {
    state.chatBusy = false;
    renderThread();
  }
}

async function loadWeek(place, delay) {
  $('#week-summary').textContent = 'Checking the forecast…';
  try {
    renderWeek(await api.weekly(place, state.bike, delay));
  } catch (err) {
    $('#week-summary').textContent = `Couldn't load the forecast: ${err.message}`;
  }
}

function wireUi() {
  $$('.tab').forEach((tab) => tab.addEventListener('click', () => {
    $$('.tab').forEach((t) => t.setAttribute('aria-selected', String(t === tab)));
    $('#pane-nav').hidden = tab.id !== 'tab-nav';
    $('#pane-chat').hidden = tab.id !== 'tab-chat';
    if (tab.id === 'tab-chat') renderThread();
  }));
  $('#f-bike').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (b) setBike(b.dataset.v);
  });
  $('#f-priority').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (b) setPriority(b.dataset.v);
  });
  $('#f-locate').addEventListener('click', async () => {
    state.here = await locate();
    if (state.here) {
      $('#f-start').value = MY_LOC;
      showHere(true);
    } else {
      showError('Location is off', 'Allow location for this page (and for your browser in your system settings), or type a town.');
    }
  });
  $('#plan-form').addEventListener('submit', (e) => {
    e.preventDefault();
    planFromForm();
  });
  $('#chat-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const input = $('#chat-input');
    sendChat(input.value);
    input.value = '';
  });
  $('#suggest').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (b) sendChat(b.textContent);
  });
  $('#week-form').addEventListener('submit', (e) => {
    e.preventDefault();
    loadWeek($('#week-place').value.trim());
  });
  $('#layer-toggle').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (b) setLayer(b.dataset.layer);
  });
  document.addEventListener('click', (e) => {
    const chatBtn = e.target.closest('[data-chat]');
    if (chatBtn) {
      const msg = state.thread[Number(chatBtn.dataset.chat)];
      if (msg?.result && state.result !== msg.result) showResult(msg.result);
      if (isNarrow()) $('#map-frame').scrollIntoView({ behavior: scrollBehavior(), block: 'center' });
      return;
    }
    const card = e.target.closest('#cards .rcard');
    if (!card) return;
    selectRoute(card.dataset.id);
    if (e.target.closest('[data-show]') && isNarrow()) $('#map-frame').scrollIntoView({ behavior: scrollBehavior(), block: 'center' });
  });
  let lastWidth = 0;
  new ResizeObserver(([entry]) => {
    const w = Math.round(entry.contentRect.width);
    if (w !== lastWidth) {
      lastWidth = w;
      renderProfile(currentRoute());
    }
  }).observe($('#profile'));
}

async function boot() {
  wireUi();
  wireSearch();
  wireRide();
  wireVoice();
  initMap();
  renderThread();
  locate().then((here) => {
    state.here = here;
    if (here && $('#f-start').value === 'Lecco, Italy') $('#f-start').value = MY_LOC;
    showHere(!state.result);
  });
  state.live = await detectApi();
  state.checked = true;
  renderMode();
  if (map.paper && !state.live) drawMockGeography();
  if (!state.live) showResult(await mockPlan(readForm(), 0));
}

boot();
