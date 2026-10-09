// Saved places and Home, kept in this browser only: one-tap chips under the boxes, a star next to the destination and a
// list in Settings. Pressing and holding the map (right-click on a computer) pins a place.
const PLACES_KEY = 'spokePlaces';
const PIN = '📍 Pinned place';
const places = { home: null, saved: [], ...loadLocal(PLACES_KEY, {}) };

const isSaved = (value) => places.saved.some((p) => p.value === value);

function storePlaces() {
  saveLocal(PLACES_KEY, places.home || places.saved.length ? places : null);
  renderPlaces();
}

// A pin has no name (there's no reverse lookup), so the rider names it.
function savePlace(name, value) {
  const label = COORDS.test(value) ? prompt('Name this place', 'Pinned place')?.trim().slice(0, 30) : name.split(',')[0].trim().slice(0, 30);
  if (!label) return;
  places.saved = [...places.saved.filter((p) => p.value !== value), { name: label, value }];
  storePlaces();
}

function removePlace(value) {
  places.saved = places.saved.filter((p) => p.value !== value);
  storePlaces();
}

function setHome(place) {
  places.home = place;
  storePlaces();
}

function renderStar() {
  const btn = $('#f-save');
  const value = placeOf($('#f-end'));
  btn.disabled = !value;
  btn.setAttribute('aria-pressed', String(isSaved(value)));
  btn.textContent = isSaved(value) ? '⭐' : '☆';
}

function renderPlaces() {
  const { home, saved } = places;
  const chips = [
    ...(home ? [{ icon: '🏠', name: 'Home', value: home.value }] : []),
    ...saved.filter((p) => p.value !== home?.value).map((p) => ({ icon: '⭐', ...p })),
  ];
  const html = chips.map((c) => `<button class="chip" type="button" data-name="${esc(c.name)}" data-value="${esc(c.value)}">${c.icon} ${esc(c.name)}</button>`).join('');
  for (const row of [$('#f-end-chips'), $('#f-start-chips')]) {
    row.innerHTML = html;
    row.hidden = !chips.length;
  }
  const homeRow = home
    ? `<li><span>🏠 Home${home.name === 'Home' ? '' : ` · ${esc(home.name)}`}</span><button class="btn btn-ghost btn-small" type="button" data-act="unhome">Remove</button></li>`
    : '';
  const rows = saved.map((p) => `<li data-value="${esc(p.value)}"><span>⭐ ${esc(p.name)}</span>
    ${p.value === home?.value ? '' : `<button class="btn btn-ghost btn-small" type="button" data-act="home" aria-label="Set ${esc(p.name)} as Home">Set as Home</button>`}
    <button class="btn btn-ghost btn-small" type="button" data-act="delete" aria-label="Delete ${esc(p.name)}">✕</button></li>`);
  $('#saved-list').innerHTML = homeRow + rows.join('') || '<li class="muted">No saved places yet.</li>';
  $('#saved-clear').hidden = !chips.length;
  renderStar();
}

function wireChips(input) {
  $(`#${input.id}-chips`).addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    setPlace(input, b.dataset.name, b.dataset.value);
    // Both boxes filled: plan right away, so the daily ride is two taps (Home, then the destination).
    if ($('#f-start').value.trim() && $('#f-end').value.trim() && !$('#plan-btn').disabled) planFromForm();
  });
}

function showPinMenu(e) {
  if (!$('#ride').hidden) return;
  const value = `${e.latlng.lat.toFixed(5)},${e.latlng.lng.toFixed(5)}`;
  const menu = document.createElement('div');
  menu.className = 'pin-menu';
  menu.innerHTML = `<b>${PIN}</b>
    <button class="btn btn-small" type="button" data-act="end">Ride here</button>
    <button class="btn btn-ghost btn-small" type="button" data-act="start">Start here</button>
    <button class="btn btn-ghost btn-small" type="button" data-act="save">⭐ Save</button>`;
  const popup = L.popup({ closeButton: false, autoPanPadding: [16, 16] }).setLatLng(e.latlng).setContent(menu).openOn(map.m);
  menu.addEventListener('click', (ev) => {
    const act = ev.target.closest('button')?.dataset.act;
    if (!act) return;
    map.m.closePopup(popup);
    if (act === 'save') {
      savePlace(PIN, value);
      return;
    }
    $('#tab-nav').click();
    setPlace($(act === 'end' ? '#f-end' : '#f-start'), PIN, value);
    if (act === 'end' && formFilled()) planFromForm();
  });
}

function wireSaved() {
  wireChips($('#f-end'));
  wireChips($('#f-start'));
  $('#f-end').addEventListener('input', renderStar);
  $('#f-save').addEventListener('click', () => {
    const value = placeOf($('#f-end'));
    if (isSaved(value)) removePlace(value);
    else savePlace($('#f-end').value.trim(), value);
  });
  $('#saved-list').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    const value = b.closest('li').dataset.value;
    if (b.dataset.act === 'unhome') setHome(null);
    else if (b.dataset.act === 'home') setHome({ ...places.saved.find((p) => p.value === value) });
    else removePlace(value);
  });
  $('#home-here').addEventListener('click', async () => {
    const here = await locate();
    if (!here) {
      $('#saved-note').textContent = 'Location is off: allow it for this page, then try again.';
      return;
    }
    state.here = here;
    showHere(false);
    setHome({ name: 'Home', value: here });
  });
  $('#saved-clear').addEventListener('click', () => {
    if (!confirm('Delete all your saved places, Home too?')) return;
    places.home = null;
    places.saved = [];
    storePlaces();
  });
  map.m?.on('contextmenu', showPinMenu);
  renderPlaces();
}
