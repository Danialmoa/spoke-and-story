function wireLogo() {
  const bike = $('#logo-bike');
  const stone = $('#stone');
  bike.addEventListener('click', async () => {
    if (bike.classList.contains('riding')) return;
    const b = bike.getBoundingClientRect();
    const s = stone.getBoundingClientRect();
    // The front tyre ends at 87% of the bike's width and the rear tyre starts at 11%.
    const liftoff = s.left - 20 - (b.left + b.width * 0.87);
    const landing = s.right + 20 - (b.left + b.width * 0.11);
    const away = window.innerWidth - b.left;
    const hang = 450;
    // fill: 'forwards' keeps the bike in place between stages instead of flashing back home for a frame.
    const roll = (from, to, duration, easing) =>
      bike.animate([{ translate: `${from}px 0` }, { translate: `${to}px 0` }], { duration, easing, fill: 'forwards' }).finished;
    bike.classList.add('riding');
    await roll(0, liftoff, liftoff * 2, 'ease-in');
    bike.animate([
      { transform: 'none', easing: 'ease-out' },
      { transform: `translateY(${-(s.height + 6)}px) rotate(-4deg)`, easing: 'ease-in' },
      { transform: 'none' },
    ], { duration: hang });
    await roll(liftoff, landing, hang, 'linear');
    await roll(landing, away, (away - landing) / 0.8, 'linear');
    await roll(-b.right, 0, Math.max(700, b.right * 4), 'ease-out');
    bike.classList.remove('riding');
  });
}
