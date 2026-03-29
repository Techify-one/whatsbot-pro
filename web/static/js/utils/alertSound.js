/**
 * Plays a two-tone siren alert using the Web Audio API.
 * @param {number} [seconds=5] - Duration in seconds.
 */
export function playTransferAlert(seconds = 5) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const duration = seconds;
  const beepOn = 0.3;
  const beepOff = 0.2;
  const cycle = beepOn + beepOff;
  const freqs = [880, 660];

  let t = ctx.currentTime;
  let i = 0;
  while (t - ctx.currentTime < duration) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = freqs[i % 2];
    osc.type = 'square';
    gain.gain.setValueAtTime(0.3, t);
    gain.gain.setValueAtTime(0, t + beepOn);
    osc.start(t);
    osc.stop(t + beepOn);
    t += cycle;
    i++;
  }

  // Close context after alert finishes
  setTimeout(() => ctx.close(), (duration + 0.5) * 1000);
}
