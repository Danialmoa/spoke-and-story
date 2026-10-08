// Sample data, used only when /api/health doesn't answer (e.g. the published preview). Delete once the backend is live.
const MOCK = {
  start: { name: 'Lecco, Italy', lat: 45.856, lon: 9.401 },
  routes: [
    { id: 'A', water_km: 24.6, forest_km: 8.2, viewpoints: 4, quiet_pct: 54, paved_pct: 98, score: 0.78,
      waypoints: [[45.856, 9.401, 214], [45.872, 9.381, 206], [45.892, 9.351, 204], [45.912, 9.333, 208], [45.936, 9.324, 212], [45.960, 9.309, 206], [45.990, 9.295, 210], [46.012, 9.290, 220], [46.040, 9.288, 202], [46.035, 9.320, 380], [46.022, 9.355, 500], [46.000, 9.385, 470], [45.975, 9.420, 586], [45.952, 9.440, 640], [45.930, 9.428, 723], [45.905, 9.420, 690], [45.880, 9.410, 420]] },
    { id: 'B', water_km: 13.1, forest_km: 15.4, viewpoints: 2, quiet_pct: 71, paved_pct: 93, score: 0.66,
      waypoints: [[45.856, 9.401, 214], [45.849, 9.376, 215], [45.846, 9.358, 237], [45.830, 9.344, 270], [45.826, 9.322, 228], [45.823, 9.292, 262], [45.820, 9.270, 285], [45.848, 9.272, 390], [45.873, 9.298, 430], [45.900, 9.292, 330], [45.926, 9.284, 215], [45.912, 9.296, 212], [45.893, 9.312, 210], [45.875, 9.334, 212], [45.862, 9.356, 214]] },
    { id: 'C', water_km: 16.8, forest_km: 12.9, viewpoints: 6, quiet_pct: 63, paved_pct: 97, score: 0.81,
      waypoints: [[45.856, 9.401, 214], [45.875, 9.412, 420], [45.903, 9.421, 690], [45.930, 9.429, 723], [45.946, 9.462, 770], [45.931, 9.488, 890], [45.958, 9.468, 700], [45.982, 9.432, 560], [46.000, 9.390, 480], [46.018, 9.345, 690], [46.040, 9.288, 202], [46.012, 9.290, 220], [45.990, 9.295, 212], [45.960, 9.309, 210], [45.936, 9.324, 205], [45.912, 9.333, 205], [45.892, 9.351, 210], [45.872, 9.381, 212]] },
  ],
  lakes: [
    { name: 'Lago di Como', label: [45.946, 9.302], angle: 62,
      shape: [[45.853, 9.398], [45.870, 9.375], [45.890, 9.345], [45.910, 9.327], [45.935, 9.318], [45.960, 9.302], [45.990, 9.288], [46.010, 9.282], [46.040, 9.280], [46.070, 9.283], [46.078, 9.262], [46.060, 9.243], [46.025, 9.236], [45.995, 9.226], [45.975, 9.205], [45.950, 9.180], [45.925, 9.155], [45.900, 9.130], [45.878, 9.105], [45.870, 9.118], [45.895, 9.148], [45.922, 9.175], [45.948, 9.203], [45.970, 9.235], [45.982, 9.257], [45.960, 9.275], [45.930, 9.290], [45.905, 9.305], [45.880, 9.330], [45.862, 9.360], [45.850, 9.385]] },
    { name: 'L. di Annone', label: [45.811, 9.330], angle: 0,
      shape: [[45.820, 9.300], [45.822, 9.318], [45.819, 9.340], [45.812, 9.358], [45.804, 9.360], [45.800, 9.343], [45.804, 9.320], [45.809, 9.302]] },
    { name: 'L. di Pusiano', label: [45.802, 9.273], angle: 0,
      shape: [[45.811, 9.258], [45.813, 9.275], [45.806, 9.290], [45.794, 9.288], [45.791, 9.270], [45.798, 9.256]] },
    { name: 'L. di Garlate', label: [45.814, 9.432], angle: 0,
      shape: [[45.838, 9.400], [45.832, 9.408], [45.815, 9.412], [45.798, 9.410], [45.796, 9.401], [45.812, 9.399], [45.828, 9.397]] },
  ],
  places: [['Lecco', 45.860, 9.416], ['Varenna', 46.010, 9.305], ['Bellano', 46.047, 9.304], ['Mandello', 45.917, 9.340], ['Ballabio', 45.900, 9.436], ['Moggio', 45.928, 9.503], ['Introbio', 45.977, 9.455], ['Cortenova', 46.003, 9.405], ['Canzo', 45.846, 9.258], ['Civate', 45.835, 9.353], ['Onno', 45.931, 9.271], ['Bellagio', 45.974, 9.248]],
  weather: [
    { temp_max: 17, temp_min: 9, rain_mm: 0.4, rain_prob: 20, wind_kmh: 11, weather_code: 2 },
    { temp_max: 13, temp_min: 10, rain_mm: 9.8, rain_prob: 90, wind_kmh: 24, weather_code: 63 },
    { temp_max: 18, temp_min: 8, rain_mm: 0, rain_prob: 5, wind_kmh: 8, weather_code: 0 },
    { temp_max: 19, temp_min: 9, rain_mm: 0.1, rain_prob: 10, wind_kmh: 10, weather_code: 1 },
    { temp_max: 12, temp_min: 8, rain_mm: 14.2, rain_prob: 95, wind_kmh: 29, weather_code: 65 },
    { temp_max: 14, temp_min: 7, rain_mm: 2.6, rain_prob: 60, wind_kmh: 18, weather_code: 80 },
    { temp_max: 16, temp_min: 6, rain_mm: 0, rain_prob: 5, wind_kmh: 7, weather_code: 0 },
  ],
  questions: { bike: 'Road, gravel or MTB?', where: 'How far do you want to ride, or where do you want to go?' },
};

function smoothLoop(waypoints, steps = 12) {
  const n = waypoints.length;
  const cr = (a, b, c, d, t) => 0.5 * (2 * b + (c - a) * t + (2 * a - 5 * b + 4 * c - d) * t * t + (3 * b - a - 3 * c + d) * t * t * t);
  const pts = [];
  for (let i = 0; i < n; i++) {
    const [p0, p1, p2, p3] = [-1, 0, 1, 2].map((k) => waypoints[(i + k + n) % n]);
    for (let s = 0; s < steps; s++) {
      const t = s / steps;
      pts.push([cr(p0[0], p1[0], p2[0], p3[0], t), cr(p0[1], p1[1], p2[1], p3[1], t), p1[2] + (p2[2] - p1[2]) * t]);
    }
  }
  pts.push(pts[0]);
  return pts.map(([lat, lon, ele], i) => [+lat.toFixed(5), +lon.toFixed(5), Math.round(ele + 2 * Math.sin(i * 0.45))]);
}

function climbStats(coords, km) {
  let climb = 0;
  let maxGrade = 0;
  let j = 0;
  for (let i = 1; i < coords.length; i++) climb += Math.max(0, coords[i][2] - coords[i - 1][2]);
  for (let i = 0; i < coords.length; i++) {
    j = Math.max(j, i);
    while (j < coords.length && km[j] - km[i] < 0.2) j++;
    if (j >= coords.length) break;
    maxGrade = Math.max(maxGrade, ((coords[j][2] - coords[i][2]) / ((km[j] - km[i]) * 1000)) * 100);
  }
  return { climb, maxGrade };
}

// Fake turn-by-turn steps at the sample route's bends (smoothLoop puts 12 points between waypoints).
function mockSteps(coords) {
  const steps = [{ index: 0, type: 11, text: 'Head out from Lecco' }];
  for (let i = 12; i < coords.length - 12; i += 12) {
    const turn = ((bearing(coords[i], coords[i + 6]) - bearing(coords[i - 6], coords[i]) + 540) % 360) - 180;
    if (Math.abs(turn) < 20) continue;
    const ahead = coords[i + 6];
    const toward = MOCK.places.reduce((a, p) => (haversineKm(ahead, [p[1], p[2]]) < haversineKm(ahead, [a[1], a[2]]) ? p : a))[0];
    const sharp = Math.abs(turn) > 60;
    const left = turn < 0;
    steps.push({
      index: i,
      type: sharp ? (left ? 0 : 1) : (left ? 4 : 5),
      text: `${sharp ? 'Turn' : 'Bear'} ${left ? 'left' : 'right'} toward ${toward}`,
    });
  }
  steps.push({ index: coords.length - 1, type: 10, text: 'Arrive back in Lecco' });
  return steps;
}

const RANKING = { scenic: (r) => -r.score, flat: (r) => r.climb_m, fast: (r) => r.duration_min };

function mockExplanation(best, req) {
  const why = {
    scenic: `Route ${best.id} gives you ${best.water_km} km beside the water and ${best.viewpoints} viewpoints.`,
    flat: `Route ${best.id} is the flattest option, with only ${best.climb_m} m of climbing.`,
    fast: `Route ${best.id} is the quickest, about ${fmtDur(best.duration_min)} of riding.`,
  }[req.priority];
  const target = req.distance_km || 50;
  const fit = req.end ? `It's ${best.distance_km} km to ${req.end}.`
    : best.fits ? `It's ${best.distance_km} km, close to the ${target} km you asked for.`
      : `It's ${best.misfit} for your ${target} km, but it's the best match I found.`;
  return `${why} ${fit}`;
}

async function mockPlan(req, delay = 900) {
  await sleep(delay);
  const speed = { road: 24, gravel: 18, mtb: 14 }[req.bike] || 20;
  const routes = MOCK.routes.map((m) => {
    const coords = smoothLoop(m.waypoints);
    const km = cumulativeKm(coords);
    const { climb, maxGrade } = climbStats(coords, km);
    const distance = km[km.length - 1];
    const target = req.distance_km || 50;
    const off = distance - target;
    const misfit = req.end || Math.abs(off) <= 0.25 * target ? null : off > 0 ? 'too long' : 'too short';
    return {
      id: m.id,
      distance_km: +distance.toFixed(1),
      duration_min: Math.round((distance / speed) * 60 + climb / 10),
      climb_m: Math.round(climb),
      max_grade_pct: +maxGrade.toFixed(1),
      paved_pct: m.paved_pct,
      quiet_pct: m.quiet_pct,
      water_km: m.water_km,
      forest_km: m.forest_km,
      viewpoints: m.viewpoints,
      score: m.score,
      misfit,
      fits: !misfit,
      coords,
      profile: coords.map((c, i) => [+km[i].toFixed(2), c[2]]),
      steps: mockSteps(coords),
    };
  });
  const rank = RANKING[req.priority] || RANKING.scenic;
  const best = [...routes].sort((a, b) => b.fits - a.fits || rank(a) - rank(b))[0];
  return { start: MOCK.start, routes, best: best.id, explanation: mockExplanation(best, req) };
}

async function mockWeekly(place, bike, delay = 700) {
  await sleep(delay);
  const today = new Date();
  const days = MOCK.weather.map((d, i) => {
    const dt = new Date(today);
    dt.setDate(today.getDate() + i);
    return { date: isoDate(dt), ...d };
  });
  const wd = (i) => weekday(days[i].date);
  const bigRide = { road: 'Long lake loop', gravel: 'Forest gravel loop', mtb: 'Trail day above the lake' }[bike] || 'Long lake loop';
  return {
    place: { name: place || MOCK.start.name, lat: MOCK.start.lat, lon: MOCK.start.lon },
    days,
    summary: `Rain on ${wd(1)} and ${wd(4)}. ${wd(2)} is the best day to ride, dry with light wind.`,
    ideas: [
      { date: days[2].date, title: bigRide, why: 'Dry, 18°C and light wind. Go long and take the lake road.' },
      { date: days[3].date, title: 'Short recovery spin', why: `Mild and dry. An easy hour before the rain on ${wd(4)}.` },
      { date: days[6].date, title: 'Easy hills ride', why: `Clear and calm. Roads and trails are dry again after ${wd(4)}'s rain.` },
    ],
  };
}

async function mockChat(messages) {
  await sleep(800);
  const said = messages.filter((m) => m.role === 'user').map((m) => m.content).join(' ').toLowerCase();
  const bike = said.match(/\b(road|gravel|mtb|mountain)\b/);
  const km = said.match(/(\d{2,3})\s*(km|k)\b/);
  const destination = /\bto [a-z]/.test(said);
  if (!bike) return { type: 'question', text: MOCK.questions.bike };
  if (!km && !destination) return { type: 'question', text: MOCK.questions.where };
  const prefer = [['water', /lake|water|river/], ['forest', /forest|tree|wood/], ['views', /view|panoram/], ['quiet', /quiet|traffic|calm/]]
    .filter(([, re]) => re.test(said))
    .map(([k]) => k);
  const request = {
    start: MOCK.start.name,
    end: null,
    distance_km: km ? +km[1] : 50,
    bike: bike[1] === 'mountain' ? 'mtb' : bike[1],
    priority: /flat|easy|tired|gentle/.test(said) ? 'flat' : /fast|quick|hurry/.test(said) ? 'fast' : 'scenic',
    prefer: prefer.length ? prefer : ['water', 'views'],
  };
  const result = await mockPlan(request, 0);
  return { type: 'plan', text: `I found ${result.routes.length} routes. Give me a moment to pick the best one for you.`, request, result };
}

async function mockPick(res) {
  await sleep(900);
  return { best: res.best, explanation: res.explanation };
}

async function mockPlaces(q) {
  await sleep(150);
  return MOCK.places
    .filter(([name]) => name.toLowerCase().includes(q.toLowerCase()))
    .map(([name, lat, lon]) => ({ name: `${name}, LC, Italy`, lat, lon }));
}

async function mockStory(route) {
  await sleep(1200);
  const at = (share) => Math.round((route.coords.length - 1) * share);
  return {
    intro: 'This is the journey around Lake Como! Fifty kilometres, a few hills, and me talking the whole way. Lucky you.',
    turn_lines: ['Hold that thought, {turn}!', 'Plot twist: {turn}.', 'Before I say anything else, {turn}.', 'Eyes up, {turn}.', 'Quick one: {turn}.'],
    finish: 'You made it back! Your legs deserve a gelato. Now go touch some grass.',
    stories: [
      { index: at(0.2), place: 'Lecco', text: 'Fun fact: Lecco is where The Betrothed is set, the most famous Italian novel. Every Italian kid had to read it at school, so wave hello to a few million tired readers.' },
      { index: at(0.45), place: 'Mandello del Lario', text: 'Coming up: Mandello del Lario, home of Moto Guzzi motorcycles since 1921. Yes, they have engines. No, you cannot borrow one for the next climb.' },
      { index: at(0.7), place: 'Varenna', text: 'Across the water is Varenna, pastel houses and people eating gelato by the lake. While you climb. Rude, honestly.' },
    ],
  };
}
