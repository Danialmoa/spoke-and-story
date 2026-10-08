const map = { m: null, street: null, sat: null, here: null, base: null, routes: null, marks: null, hover: null, tilesOk: false, paper: false, geoDrawn: false };
const FLAG = '<svg viewBox="0 0 22 34" aria-hidden="true"><path class="flag-pole" d="M3 2v31"/><path class="flag-cloth" d="M4 3h15l-4 5 4 5H4z"/></svg>';

function initMap() {
  if (!window.L) {
    setPaperMode();
    return;
  }
  const m = L.map('map', { zoomControl: false, scrollWheelZoom: false }).setView([45.94, 9.36], 11);
  m.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
  L.control.zoom({ position: 'bottomright' }).addTo(m);
  L.control.scale({ position: 'bottomleft', imperial: false }).addTo(m);
  map.street = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  });
  map.sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 18, attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
  });
  let tileErrors = 0;
  map.street.on('tileload', () => { map.tilesOk = true; });
  map.street.on('tileerror', () => {
    tileErrors += 1;
    if (!map.tilesOk && tileErrors >= 3) setPaperMode();
  });
  map.street.addTo(m);
  setTimeout(() => { if (!map.tilesOk) setPaperMode(); }, 6000);
  map.base = L.layerGroup().addTo(m);
  map.routes = L.layerGroup().addTo(m);
  map.marks = L.layerGroup().addTo(m);
  map.hover = L.circleMarker([0, 0], { radius: 7, className: 'hover-dot', interactive: false });
  map.m = m;
}

function setPaperMode() {
  if (map.paper) return;
  map.paper = true;
  if (map.m) {
    map.m.removeLayer(map.street);
    map.m.removeLayer(map.sat);
  }
  $('#map-frame').classList.add('paper');
  $('#map-frame').classList.remove('is-sat');
  $('#layer-toggle').hidden = true;
  $('#paper-note').hidden = false;
  if (state.checked && !state.live) drawMockGeography();
}

function setLayer(kind) {
  if (!map.m || map.paper) return;
  const on = kind === 'sat' ? map.sat : map.street;
  const off = kind === 'sat' ? map.street : map.sat;
  map.m.removeLayer(off);
  if (!map.m.hasLayer(on)) on.addTo(map.m);
  $('#map-frame').classList.toggle('is-sat', kind === 'sat');
  $$('#layer-toggle button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.layer === kind)));
}

function mapLabel(latlng, text, cls, angle = 0) {
  const html = `<span style="transform: translate(-50%, -50%) rotate(${angle}deg)">${esc(text)}</span>`;
  L.marker(latlng, { icon: L.divIcon({ className: cls, html, iconSize: [0, 0] }), interactive: false, keyboard: false }).addTo(map.base);
}

function drawMockGeography() {
  if (!map.m || map.geoDrawn) return;
  map.geoDrawn = true;
  MOCK.lakes.forEach((lake) => {
    L.polygon(lake.shape, { className: 'lake', interactive: false }).addTo(map.base).bringToBack();
    mapLabel(lake.label, lake.name, 'water-label', lake.angle);
  });
  MOCK.places.forEach(([name, lat, lon]) => mapLabel([lat, lon], name, 'place-label'));
}

function pin(latlng, text) {
  const icon = L.divIcon({ className: 'start-pin', html: `<div class="pin">${FLAG}<span>${text}</span></div>`, iconSize: [0, 0] });
  L.marker(latlng, { icon, interactive: false, keyboard: false }).addTo(map.marks);
}

function addKmMarkers(route) {
  const kms = routeKm(route);
  const total = kms[kms.length - 1];
  for (let k = 10; k < total - 2; k += 10) {
    const [lat, lon] = route.coords[nearestIndex(kms, k)];
    const icon = L.divIcon({ className: 'km-mark', html: `<span>${k}</span>`, iconSize: [0, 0] });
    L.marker([lat, lon], { icon, interactive: false, keyboard: false }).addTo(map.marks);
  }
}

function drawRoutes() {
  if (!map.m) return;
  map.routes.clearLayers();
  map.marks.clearLayers();
  hideHover();
  const res = state.result;
  if (!res) return;
  const latlngs = (r) => r.coords.map((c) => [c[0], c[1]]);
  const sel = currentRoute();
  res.routes.filter((r) => r !== sel).forEach((r) => {
    L.polyline(latlngs(r), { className: 'route-alt' }).on('click', () => selectRoute(r.id)).addTo(map.routes);
  });
  if (sel) {
    L.polyline(latlngs(sel), { className: 'route-casing', interactive: false }).addTo(map.routes);
    L.polyline(latlngs(sel), { className: 'route-sel', interactive: false }).addTo(map.routes);
    addKmMarkers(sel);
    const first = sel.coords[0];
    const last = sel.coords[sel.coords.length - 1];
    if (haversineKm(first, last) > 0.3) pin([last[0], last[1]], 'Finish');
  }
  pin([res.start.lat, res.start.lon], 'Start');
}

function fitRoutes() {
  if (!map.m || !state.result) return;
  const all = state.result.routes.flatMap((r) => r.coords.map((c) => [c[0], c[1]]));
  if (all.length) map.m.fitBounds(L.latLngBounds(all), { padding: [30, 30] });
}

function showHover(route, km) {
  if (!map.m) return;
  const [lat, lon] = route.coords[nearestIndex(routeKm(route), km)];
  map.hover.setLatLng([lat, lon]);
  if (!map.m.hasLayer(map.hover)) map.hover.addTo(map.m);
}

function showHere(pan) {
  if (!map.m || !state.here) return;
  const pos = state.here.split(',').map(Number);
  if (!map.here) {
    const icon = L.divIcon({ className: 'here', html: '<span title="You are here"></span>', iconSize: [0, 0] });
    map.here = L.marker(pos, { icon, interactive: false, keyboard: false, zIndexOffset: 900 }).addTo(map.m);
  }
  map.here.setLatLng(pos);
  if (pan) map.m.setView(pos, Math.max(map.m.getZoom(), 13));
}

function hideHover() {
  if (map.m && map.m.hasLayer(map.hover)) map.m.removeLayer(map.hover);
}
