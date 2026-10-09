// "Before you go": fun local news, the destination, the weather on arrival and water stops. Fetched when routes show,
// read on a card, and told by Santa right after the intro.
const briefs = new WeakMap();
const briefsDone = new WeakMap();
const BRIEF_LABELS = { news: 'News', destination: 'There', weather: 'Weather', water: 'Water', repair: 'Repair' };

function ensureBrief(route) {
  if (!briefs.has(route)) {
    const brief = api.brief(state.req || readForm(), route).catch(() => null);
    briefs.set(route, brief);
    brief.then((b) => {
      briefsDone.set(route, b);
      if (b?.spoken) prepare(b.spoken);
      if (route === currentRoute()) renderBrief(b);
    });
  }
  if (route === currentRoute()) renderBrief(briefsDone.get(route), !briefsDone.has(route));
  return briefs.get(route);
}

function renderBrief(brief, loading = false) {
  $('#brief').hidden = !loading && !brief?.items?.length;
  $('#brief-items').innerHTML = loading
    ? '<p class="muted">Checking the local news, the weather when you arrive and the water stops…</p>'
    : (brief?.items || []).map((i) => `<p><b>${esc(BRIEF_LABELS[i.kind] || i.kind)}</b> ${esc(i.text)}</p>`).join('');
}

// Once, after the intro, while the rider is still near the start. Queued only once voiced (like live.js), so it never
// holds up a turn.
function sayBrief(route) {
  briefs.get(route)?.then(async (b) => {
    if (!b?.spoken || ride.planned !== route || $('#ride').hidden || ride.km >= 0.5) return;
    await prepare(b.spoken, { first: true });
    if (ride.planned === route && !$('#ride').hidden && ride.km < 0.5) say(b.spoken, { title: 'Before you go' });
  });
}

// Sample brief for the sample routes around Lecco, used only without a backend.
async function mockBrief() {
  await sleep(900);
  return {
    items: [
      { kind: 'weather', text: 'Back in Lecco around 17:40: 21°C, dry and calm, with an hour of daylight to spare.' },
      { kind: 'water', text: 'First fountain at km 1.4, then plenty along the lake. No excuse for a dry throat.' },
      { kind: 'repair', text: 'A bike repair station waits at km 2.2, in case your chain gets dramatic.' },
    ],
    spoken: 'Before we roll: the weather gods have signed off on this one. Twenty-one degrees, dry roads and a whole hour of daylight to spare. The lake has fountains lined up like a pit crew, so drink up, and a repair station stands guard at kilometre two. Now, legs, the tale is waiting.',
  };
}
