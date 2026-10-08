function wireLogo() {
  const bike = $('#logo-bike');
  const pill = $('#mode');
  bike.addEventListener('click', async () => {
    if (bike.classList.contains('riding')) return;
    const b = bike.getBoundingClientRect();
    const p = pill.getBoundingClientRect();
    const takeoff = p.left - b.right + 6;
    const away = window.innerWidth - b.left;
    const rise = Math.max(30, b.bottom - p.top + 10);
    // fill: 'forwards' keeps the bike in place between stages instead of flashing back home for a frame.
    const ride = (frames, opts) => bike.animate(frames, { fill: 'forwards', ...opts }).finished;
    bike.classList.add('riding');
    await ride([{ transform: 'none' }, { transform: `translateX(${takeoff}px)` }], { duration: takeoff * 2, easing: 'ease-in' });
    pill.animate([{ transform: 'none' }, { transform: 'translateY(3px) rotate(-6deg)' }, { transform: 'none' }], { duration: 350, easing: 'ease-out' });
    await ride([
      { transform: `translateX(${takeoff}px)` },
      { transform: `translate(${takeoff + (away - takeoff) * 0.3}px, ${-rise * 0.6}px) rotate(-20deg)` },
      { transform: `translate(${away}px, ${-rise}px) rotate(-10deg)` },
    ], { duration: 700, easing: 'ease-out' });
    await ride([{ transform: `translateX(${-b.right}px)` }, { transform: 'none' }], { duration: Math.max(700, b.right * 4), easing: 'ease-out' });
    bike.classList.remove('riding');
  });
}
