function setSearched(on) {
  state.searched = on;
  $('#plan-btn').textContent = on ? 'Start ride ▶' : "Let's search →";
}

async function planFromForm() {
  showError(null);
  setLoading(true);
  if (isNarrow()) $('#map-frame').scrollIntoView({ behavior: scrollBehavior(), block: 'center' });
  const req = readForm();
  let res;
  try {
    res = await api.plan(req);
  } catch (err) {
    showError("Couldn't plan that ride", err.message);
    return;
  } finally {
    setLoading(false);
  }
  state.req = req;
  showResult(res);
  if (state.result !== res) return;
  setSearched(true);
  pickBest(req, res);
}

// The agent's pick is slow (15-25 s), so routes are shown first and the pick fills in when it arrives.
async function pickBest(req, res) {
  try {
    const pick = await api.pick(req, res);
    if (state.result !== res) return null;
    Object.assign(res, pick);
    res.explanation ||= `Route ${res.best} looks like the best match for you.`;
    showPick(res);
    renderCards();
    if ($('#ride').hidden) selectRoute(res.best);
    ensureStory(currentRoute());
    return res;
  } catch (err) {
    if (state.result === res) $('#pick-text').textContent = `Couldn't get the agent's pick: ${err.message}`;
    return null;
  }
}

// Our own message instead of the browser's "Please fill in this field" bubble (the form is novalidate).
function markMissing(input, missing) {
  input.classList.toggle('is-missing', missing);
  $(`#${input.id}-msg`).hidden = !missing;
}

function formFilled() {
  const empty = [$('#f-end'), $('#f-start')].filter((input) => !input.value.trim());
  [$('#f-end'), $('#f-start')].forEach((input) => markMissing(input, empty.includes(input)));
  empty[0]?.focus();
  return !empty.length;
}

function placesNear(input) {
  return input.id === 'f-end' ? readForm().start : state.here;
}

function wirePlaces(input) {
  const list = $(`#${input.id}-list`);
  let items = [];
  let active = -1;
  let timer = 0;

  const render = () => {
    list.innerHTML = items
      .map((p, i) => `<li role="option" id="${input.id}-opt-${i}" data-i="${i}" aria-selected="${i === active}">${esc(p.name)}</li>`)
      .join('');
    list.hidden = !items.length;
    input.setAttribute('aria-expanded', String(!list.hidden));
    if (active >= 0) input.setAttribute('aria-activedescendant', `${input.id}-opt-${active}`);
    else input.removeAttribute('aria-activedescendant');
  };
  const close = () => {
    items = [];
    active = -1;
    render();
  };
  const choose = (i) => {
    input.value = items[i].name;
    close();
    setSearched(false);
  };

  input.addEventListener('input', () => {
    setSearched(false);
    markMissing(input, false);
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 3 || q === MY_LOC) {
      close();
      return;
    }
    timer = setTimeout(async () => {
      let found = [];
      try {
        found = await api.places(q, placesNear(input));
      } catch {
        found = [];
      }
      if (input.value.trim() !== q || document.activeElement !== input) return;
      items = found.slice(0, 5);
      active = -1;
      render();
    }, 350);
  });
  input.addEventListener('keydown', (e) => {
    if (list.hidden) return;
    const n = items.length;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      active = e.key === 'ArrowDown' ? (active + 1) % n : active <= 0 ? n - 1 : active - 1;
      render();
    } else if (e.key === 'Enter' && active >= 0) {
      e.preventDefault();
      choose(active);
    } else if (e.key === 'Escape') {
      close();
    }
  });
  list.addEventListener('pointerdown', (e) => {
    const li = e.target.closest('li');
    if (!li) return;
    e.preventDefault();
    choose(Number(li.dataset.i));
  });
  input.addEventListener('blur', () => setTimeout(close, 150));
}

function wireSearch() {
  wirePlaces($('#f-end'));
  wirePlaces($('#f-start'));
  $('#f-bike').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    setBike(b.dataset.v);
    setSearched(false);
  });
  $('#f-priority').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    setPriority(b.dataset.v);
    setSearched(false);
  });
  $('#f-locate').addEventListener('click', async () => {
    state.here = await locate();
    if (state.here) {
      $('#f-start').value = MY_LOC;
      markMissing($('#f-start'), false);
      setSearched(false);
      showHere(true);
    } else {
      showError('Location is off', 'Allow location for this page (and for your browser in your system settings), or type a town.');
    }
  });
  // Handled on click so "Start ride" also works for a chat loop, where the required destination is empty.
  $('#plan-btn').addEventListener('click', (e) => {
    if (!state.searched || !currentRoute()) return;
    e.preventDefault();
    startRide(false);
  });
  $('#plan-form').addEventListener('submit', (e) => {
    e.preventDefault();
    if (formFilled()) planFromForm();
  });
}
