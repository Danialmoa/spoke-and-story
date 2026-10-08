(() => {
  // Everything is drawn in a 130px-tall logical space and scaled to the canvas, so physics feel the same at any size.
  const HEIGHT = 130;
  const GROUND = HEIGHT - 22;
  // The bike is the header logo's pixel art: 38x29 pixels, rear axle at (10.5, 21.5), wheels 16 apart with radius 6.4.
  const PX = 1.75;
  const R = 6.4 * PX;
  const BASE = 16 * PX;
  const BIKE_X = 44;
  const GRAVITY = 2300;
  const JUMP = 480;
  const PX_PER_M = 20;
  const FONT = '600 12px "IBM Plex Mono", monospace';
  // Stones in the bike's pixel size: small (comes in pairs), medium, and big with moss. K is the outline.
  const STONES = [
    [
      '..KKKK..',
      '.KssSSK.',
      'KsSSSSdK',
      'KSSSSddK',
      'KKKKKKKK',
    ],
    [
      '....KKKK....',
      '..KKssSSKK..',
      '.KssSSSSSdK.',
      'KsSSSSSSSSdK',
      'KSSSSdSSSddK',
      'KSSSSSSSdddK',
      'KKKKKKKKKKKK',
    ],
    [
      '......L.g.....',
      '.....gFgFg....',
      '....KFgFgFKK..',
      '...KssSSSSSSK.',
      '..KsSSSSSSSSdK',
      '.KsSSSSSSdSSdK',
      'KsSSSSSSSdSddK',
      'KSSSSSSSSSSddK',
      'KSSdSSSSSSdddK',
      'KKKKKKKKKKKKKK',
    ],
  ];
  const STONE_COLORS = { S: '#8E9893', s: '#C3CBC6', d: '#66706B', F: '#5F7F45', g: '#97B87A', L: '#D4ECB4' };

  let canvas, ctx, colors, s, bikeFrames, stoneArt;
  let raf = 0;
  let last = 0;
  let W = 0;
  let best = 0;

  const cssVar = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

  function reset() {
    s = { dist: 0, speed: 260, y: 0, vy: 0, stones: [], gap: W * 0.7, crashed: false, started: false };
  }

  function addStones() {
    const pair = Math.random() < 0.25;
    let x = W + 10;
    for (let i = 0; i < (pair ? 2 : 1); i++) {
      const art = stoneArt[pair ? 0 : 1 + Math.floor(Math.random() * 2)];
      s.stones.push({ x, art, w: art.width * PX, h: art.height * PX });
      x += art.width * PX - 2;
    }
    // Always leaves room to land and jump again: a jump covers about 0.42 s of travel.
    s.gap = s.speed * (0.75 + Math.random() * 0.9) + 90;
  }

  function jump() {
    if (s.crashed) reset();
    s.started = true;
    if (s.y === 0) s.vy = JUMP;
  }

  function update(dt) {
    s.speed = Math.min(620, s.speed + 9 * dt);
    const move = s.speed * dt;
    s.dist += move;
    s.vy -= GRAVITY * dt;
    s.y = Math.max(0, s.y + s.vy * dt);
    if (s.y === 0) s.vy = 0;
    for (const st of s.stones) st.x -= move;
    s.stones = s.stones.filter((st) => st.x + st.w > -10);
    if ((s.gap -= move) <= 0) addStones();
    const left = BIKE_X - R + 5;
    const right = BIKE_X + BASE + R - 5;
    s.crashed = s.stones.some((st) => st.x + 3 < right && st.x + st.w - 3 > left && s.y < st.h - 3);
    if (s.crashed) best = Math.max(best, Math.floor(s.dist / PX_PER_M));
  }

  function path(points, width = 2.5, stroke = colors.ink) {
    ctx.lineWidth = width;
    ctx.strokeStyle = stroke;
    ctx.beginPath();
    points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.stroke();
  }

  // Images from the logo SVG: standing still, then two riding frames. Outlines take the game's ink so they show on the dark map too.
  function loadBikeFrames() {
    return [['f1'], ['f1', 'k1'], ['f2', 'k2']].map((keep) => {
      const svg = document.querySelector('#logo-bike svg').cloneNode(true);
      svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
      svg.setAttribute('width', '38');
      svg.setAttribute('height', '29');
      svg.querySelectorAll('g[class]').forEach((g) => keep.includes(g.getAttribute('class')) || g.remove());
      const img = new Image();
      img.src = `data:image/svg+xml,${encodeURIComponent(svg.outerHTML.replaceAll('#17241F', colors.ink))}`;
      return img;
    });
  }

  function loadStoneArt() {
    return STONES.map((rows) => {
      const art = document.createElement('canvas');
      art.width = rows[0].length;
      art.height = rows.length;
      const g = art.getContext('2d');
      rows.forEach((row, y) => [...row].forEach((ch, x) => {
        if (ch === '.') return;
        g.fillStyle = ch === 'K' ? colors.ink : STONE_COLORS[ch];
        g.fillRect(x, y, 1, 1);
      }));
      return art;
    });
  }

  function drawBiker() {
    const img = bikeFrames[s.started && !s.crashed ? 1 + (Math.floor(s.dist / 30) % 2) : 0];
    ctx.drawImage(img, BIKE_X - 10.5 * PX, GROUND - 28 * PX - s.y, 38 * PX, 29 * PX);
  }

  function draw() {
    ctx.clearRect(0, 0, W, HEIGHT);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    path([[0, GROUND], [W, GROUND]], 2);
    ctx.strokeStyle = colors.muted;
    ctx.beginPath();
    for (let x = -(s.dist % 34); x < W; x += 34) {
      ctx.moveTo(x, GROUND + 8);
      ctx.lineTo(x + 14, GROUND + 8);
    }
    ctx.stroke();
    for (const st of s.stones) ctx.drawImage(st.art, st.x, GROUND - st.h, st.w, st.h);
    drawBiker();
    ctx.font = FONT;
    ctx.textBaseline = 'top';
    ctx.textAlign = 'right';
    ctx.fillStyle = colors.ink;
    ctx.fillText(`${Math.floor(s.dist / PX_PER_M)} m`, W - 10, 8);
    if (best) {
      ctx.fillStyle = colors.muted;
      ctx.fillText(`best ${best} m`, W - 10, 24);
    }
    ctx.textAlign = 'center';
    if (s.crashed) {
      ctx.fillStyle = colors.accent;
      ctx.fillText('CRASH! tap to ride again', W / 2, HEIGHT * 0.35);
    } else if (!s.started) {
      ctx.fillStyle = colors.accent;
      ctx.fillText('TAP TO PLAY ▶', W / 2, HEIGHT * 0.25);
      ctx.fillStyle = colors.muted;
      ctx.fillText('then tap or press space to jump', W / 2, HEIGHT * 0.25 + 18);
    }
  }

  function frame(t) {
    const dt = Math.min(0.05, (t - (last || t)) / 1000);
    last = t;
    if (s.started && !s.crashed) update(dt);
    draw();
    raf = requestAnimationFrame(frame);
  }

  function onPointer(e) {
    e.preventDefault();
    jump();
  }

  function onKey(e) {
    if (e.code !== 'Space' && e.code !== 'ArrowUp') return;
    if (e.target.closest?.('input, textarea, [contenteditable]')) return;
    e.preventDefault();
    jump();
  }

  function start(c) {
    stop();
    canvas = c;
    // A hidden canvas has no size yet; fall back to the default 420x130.
    const cssW = c.clientWidth || 420;
    const cssH = c.clientHeight || HEIGHT;
    const dpr = window.devicePixelRatio || 1;
    const k = cssH / HEIGHT;
    c.width = Math.round(cssW * dpr);
    c.height = Math.round(cssH * dpr);
    ctx = c.getContext('2d');
    ctx.setTransform(dpr * k, 0, 0, dpr * k, 0, 0);
    ctx.imageSmoothingEnabled = false;
    W = cssW / k;
    colors = { ink: cssVar('--map-ink', '#17241F'), accent: cssVar('--route', '#CF4F3B'), muted: cssVar('--muted', '#5B6A64') };
    bikeFrames = loadBikeFrames();
    stoneArt = loadStoneArt();
    reset();
    c.addEventListener('pointerdown', onPointer);
    window.addEventListener('keydown', onKey);
    last = 0;
    raf = requestAnimationFrame(frame);
  }

  function stop() {
    cancelAnimationFrame(raf);
    raf = 0;
    canvas?.removeEventListener('pointerdown', onPointer);
    window.removeEventListener('keydown', onKey);
  }

  window.BikeGame = { start, stop };
})();
