// Settings: a short list in a sheet, opened from the header or, during a ride, from the ride panel.
let settingsOpener = null;

function openSettings(opener) {
  settingsOpener = opener;
  $('#settings').hidden = false;
  $$('.settings-open').forEach((b) => b.setAttribute('aria-expanded', 'true'));
  // Before the page knows whether a server is there, it can't tell whether ElevenLabs is.
  if (state.checked) checkEleven();
  renderVoiceStatus();
  ($('#set-voice').disabled ? $('#settings-close') : $('#set-voice')).focus();
}

function closeSettings() {
  if ($('#settings').hidden) return;
  $('#settings').hidden = true;
  $$('.settings-open').forEach((b) => b.setAttribute('aria-expanded', 'false'));
  settingsOpener?.focus();
}

function wireSettings() {
  $$('.settings-open').forEach((b) => b.addEventListener('click', () => ($('#settings').hidden ? openSettings(b) : closeSettings())));
  $('#settings-close').addEventListener('click', closeSettings);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeSettings();
  });
  $('#set-voice').addEventListener('click', (e) => chooseVoice(e.currentTarget.getAttribute('aria-pressed') === 'true' ? 'eleven' : 'kokoro'));
  renderVoiceStatus();
}
