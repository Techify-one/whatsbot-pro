import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

const SPEEDS = [1, 1.5, 2];

export function AudioPlayer({ src, isLocalBlob }) {
  const audioRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [speedIdx, setSpeedIdx] = useState(0);

  const audioSrc = isLocalBlob ? src : '/' + src;

  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onMeta = () => setDuration(a.duration || 0);
    const onTime = () => setCurrentTime(a.currentTime || 0);
    const onEnd = () => { setPlaying(false); setCurrentTime(0); };
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    a.addEventListener('loadedmetadata', onMeta);
    a.addEventListener('timeupdate', onTime);
    a.addEventListener('ended', onEnd);
    a.addEventListener('play', onPlay);
    a.addEventListener('pause', onPause);
    // If metadata already loaded
    if (a.readyState >= 1) onMeta();
    return () => {
      a.removeEventListener('loadedmetadata', onMeta);
      a.removeEventListener('timeupdate', onTime);
      a.removeEventListener('ended', onEnd);
      a.removeEventListener('play', onPlay);
      a.removeEventListener('pause', onPause);
    };
  }, []);

  function togglePlay() {
    const a = audioRef.current;
    if (!a) return;
    if (playing) { a.pause(); } else { a.play(); }
  }

  function cycleSpeed() {
    const next = (speedIdx + 1) % SPEEDS.length;
    setSpeedIdx(next);
    if (audioRef.current) audioRef.current.playbackRate = SPEEDS[next];
  }

  function seek(e) {
    const a = audioRef.current;
    if (!a || !duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    a.currentTime = (x / rect.width) * duration;
  }

  function fmt(s) {
    if (!s || !isFinite(s)) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;
  const speed = SPEEDS[speedIdx];

  return html`
    <div class="flex items-center gap-[8px] mb-1" style="min-width:240px">
      <audio ref=${audioRef} preload="metadata">
        <source src="${audioSrc}" type="audio/wav" />
        <source src="${audioSrc}" type="audio/ogg" />
        <source src="${audioSrc}" type="audio/mpeg" />
      </audio>

      <!-- Play/Pause -->
      <button type="button" onClick=${togglePlay}
        class="w-[32px] h-[32px] flex items-center justify-center rounded-full shrink-0 text-wa-teal hover:text-[#06a884] transition-colors">
        ${playing ? html`
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <rect x="3" y="2" width="4" height="12" rx="1" />
            <rect x="9" y="2" width="4" height="12" rx="1" />
          </svg>
        ` : html`
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <path d="M4 2.5v11l9-5.5-9-5.5z" />
          </svg>
        `}
      </button>

      <!-- Progress bar -->
      <div class="flex-1 flex flex-col gap-[2px] min-w-0">
        <div class="relative h-[4px] bg-[#d9d9d9] rounded-full cursor-pointer group" onClick=${seek}>
          <div class="absolute left-0 top-0 h-full bg-wa-teal rounded-full transition-[width] duration-100"
            style="width: ${progress}%"></div>
          <div class="absolute top-1/2 -translate-y-1/2 w-[12px] h-[12px] bg-wa-teal rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
            style="left: calc(${progress}% - 6px)"></div>
        </div>
        <div class="flex justify-between">
          <span class="text-[11px] text-wa-secondary">${fmt(playing ? currentTime : duration)}</span>
        </div>
      </div>

      <!-- Speed button -->
      <button type="button" onClick=${cycleSpeed}
        class="text-[11px] font-medium px-[6px] py-[2px] rounded-full shrink-0 transition-colors
          ${speed === 1 ? 'text-wa-secondary bg-[#e9edef]' : 'text-white bg-wa-teal'}">
        ${speed}x
      </button>
    </div>
  `;
}
