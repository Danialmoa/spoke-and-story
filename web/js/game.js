(() => {
  // Everything is drawn in a 130px-tall logical space and scaled to the canvas, so physics feel the same at any size.
  const HEIGHT = 130;
  const GROUND = HEIGHT - 22;
  const R = 9;
  const BASE = 30;
  const BIKE_X = 44;
  const GRAVITY = 2300;
  const JUMP = 480;
  const PX_PER_M = 20;
  const FONT = '600 12px "IBM Plex Mono", monospace';

  let canvas, ctx, colors, s;
  let raf = 0;
  let last = 0;
  let W = 0;
  let best = 0;

  const cssVar = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

  function reset() {
    s = { dist: 0, speed: 260, y: 0, vy: 0, stones: [], gap: W * 0.7, crashed: false, tapped: false, time: 0 };
  }

  function addStones() {
    const pair = Math.random() < 0.25;
    let x = W + 10;
    for (let i = 0; i < (pair ? 2 : 1); i++) {
      const h = pair ? 8 + Math.random() * 4 : 9 + Math.random() * 9;
      const w = h * 1.4 + Math.random() * (pair ? 4 : 6);
      s.stones.push({ x, w, h });
      x += w - 2;
    }
    // Always leaves room to land and jump again: a jump covers about 0.42 s of travel.
    s.gap = s.speed * (0.75 + Math.random() * 0.9) + 90;
  }

  function jump() {
    if (s.crashed) return reset();
    s.tapped = true;
    if (s.y === 0) s.vy = JUMP;
  }

  function update(dt) {
    s.time += dt;
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

  function drawBiker() {
    const wy = GROUND - R - s.y;
    const rx = BIKE_X;
    const fx = BIKE_X + BASE;
    const angle = s.dist / R;
    for (const hx of [rx, fx]) {
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = colors.ink;
      ctx.beginPath();
      ctx.arc(hx, wy, R, 0, Math.PI * 2);
      ctx.stroke();
      for (let k = 0; k < 2; k++) {
        const a = angle + (k * Math.PI) / 2;
        path([[hx - Math.cos(a) * R, wy - Math.sin(a) * R], [hx + Math.cos(a) * R, wy + Math.sin(a) * R]], 1.2);
      }
    }
    const bb = [rx + 13, wy + 1];
    const seat = [rx + 9, wy - 15];
    const head = [fx - 5, wy - 13];
    const bar = [fx - 3, wy - 18];
    path([[rx, wy], bb, seat, [rx, wy]]);
    path([seat, head, bb]);
    path([bar, head, [fx, wy]]);
    const hip = [seat[0] + 1, seat[1] - 2];
    const shoulder = [rx + 23, wy - 30];
    const pedal = [bb[0] + Math.cos(angle) * 5, bb[1] + Math.sin(angle) * 5];
    path([hip, [rx + 20, wy - 8], pedal]);
    path([shoulder, bar], 2.2);
    path([hip, shoulder], 5, colors.accent);
    ctx.fillStyle = colors.ink;
    ctx.beginPath();
    ctx.arc(shoulder[0] + 4, shoulder[1] - 6, 4.5, 0, Math.PI * 2);
    ctx.fill();
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
    for (const st of s.stones) {
      ctx.beginPath();
      ctx.moveTo(st.x, GROUND);
      ctx.quadraticCurveTo(st.x + st.w * 0.04, GROUND - st.h, st.x + st.w * 0.42, GROUND - st.h);
      ctx.quadraticCurveTo(st.x + st.w * 0.98, GROUND - st.h * 0.95, st.x + st.w, GROUND);
      ctx.closePath();
      ctx.fillStyle = colors.muted;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = colors.ink;
      ctx.stroke();
    }
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
    ctx.fillStyle = s.crashed ? colors.accent : colors.ink;
    if (s.crashed) ctx.fillText('CRASH! tap to ride again', W / 2, HEIGHT * 0.35);
    else if (!s.tapped && s.time < 3) ctx.fillText('tap to jump', W / 2, HEIGHT * 0.35);
  }

  function frame(t) {
    const dt = Math.min(0.05, (t - (last || t)) / 1000);
    last = t;
    if (!s.crashed) update(dt);
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
    W = cssW / k;
    colors = { ink: cssVar('--map-ink', '#17241F'), accent: cssVar('--route', '#CF4F3B'), muted: cssVar('--muted', '#5B6A64') };
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
