import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import {
  isSeekable, ratioFromPointer, timeFromRatio, progressPercent, displayTime, formatClock, nudge,
} from '../../services/audioScrub.js';

const html = htm.bind(h);

const SPEEDS = [1, 1.5, 2];
// Passo das setas do teclado — mesma ordem de grandeza do gesto de retroceder
// "um pouquinho" que originou o plano 138.
const NUDGE_SECONDS = 5;

/**
 * Player de áudio do painel (bolha do chat, nota privada e bandeja de anexo).
 *
 * @param {Object} props
 * @param {string} props.src
 * @param {boolean} [props.isLocalBlob]
 * @param {boolean} [props.disabled] - em modo SELEÇÃO de mensagem a barra fica
 *   inerte e o clique/arraste pertence à linha inteira (plano 138 · P3): ali o
 *   operador está selecionando, não ouvindo, e um scrubber vivo criaria uma
 *   faixa de 20px onde clicar não marca a mensagem.
 */
export function AudioPlayer({ src, isLocalBlob, disabled = false }) {
  const audioRef = useRef(null);
  const barRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [speedIdx, setSpeedIdx] = useState(0);
  const speed = SPEEDS[speedIdx];
  // Espelho em ref para o listener de metadados poder reaplicar a velocidade
  // sem entrar nas deps do efeito (re-registrar listener a cada 1x→1.5x→2x).
  const speedRef = useRef(speed);
  speedRef.current = speed;
  // `null` = não está arrastando. Um `0` legítimo (arrastou até o começo) é
  // falsy, então em toda comparação daqui para baixo o teste é contra `null`.
  const [scrubRatio, setScrubRatio] = useState(null);

  const audioSrc = isLocalBlob ? src : '/' + src;

  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onMeta = () => {
      setDuration(a.duration || 0);
      // G3 — todo `load()` devolve o playbackRate ao padrão. Sem reaplicar, o
      // chip segue anunciando 2x com o áudio tocando em 1x.
      a.playbackRate = speedRef.current;
    };
    const onTime = () => setCurrentTime(a.currentTime || 0);
    const onEnd = () => { setPlaying(false); setCurrentTime(0); };
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    a.addEventListener('loadedmetadata', onMeta);
    // G4 — em Ogg a duração costuma ser REFINADA depois do loadedmetadata (o
    // granulepos final só aparece com o arquivo todo). Sem escutar isto, a
    // barra fica calibrada para uma duração errada até o fim da reprodução.
    a.addEventListener('durationchange', onMeta);
    a.addEventListener('timeupdate', onTime);
    a.addEventListener('ended', onEnd);
    a.addEventListener('play', onPlay);
    a.addEventListener('pause', onPause);
    // If metadata already loaded
    if (a.readyState >= 1) onMeta();
    return () => {
      a.removeEventListener('loadedmetadata', onMeta);
      a.removeEventListener('durationchange', onMeta);
      a.removeEventListener('timeupdate', onTime);
      a.removeEventListener('ended', onEnd);
      a.removeEventListener('play', onPlay);
      a.removeEventListener('pause', onPause);
    };
  }, []);

  // G2 — a bolha otimista de um áudio enviado nasce apontando para um blob
  // local e é reconciliada, segundos depois, para o caminho do servidor. Mudar
  // o `src` de um <audio> exige `load()` para o elemento reler o arquivo; sem
  // isso duração e posição continuam sendo as do blob antigo e a barra fica
  // calibrada para outro arquivo. Efeito próprio (e não deps novas no de cima)
  // para não re-registrar os cinco listeners a cada troca.
  const firstSrcRef = useRef(true);
  useEffect(() => {
    if (firstSrcRef.current) { firstSrcRef.current = false; return; }
    setCurrentTime(0);
    setDuration(0);
    setScrub(null);
    const a = audioRef.current;
    if (!a) return;
    a.load();
    a.playbackRate = speedRef.current;
  }, [audioSrc]);

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

  // ── Scrubber (plano 138 · F2) ─────────────────────────────────────────────
  //
  // ⚠️ AQUI ESTAVA O BUG RELATADO. Isto era um `onClick` numa faixa de 4px de
  // altura. Num ARRASTE — o gesto que todo mundo usa, porque é o do WhatsApp e
  // o do Telegram — o `mouseup` sai facilmente dos 4px, e a regra do DOM manda
  // o `click` para o ANCESTRAL COMUM do alvo do mousedown e do alvo do mouseup:
  // a coluna flex logo acima, que não tem handler nenhum. O seek NUNCA rodava.
  // Sem erro, sem log: silêncio total, e o áudio seguindo em frente — que é
  // exatamente o que o operador descreveu.
  //
  // `setPointerCapture` é o conserto: com o ponteiro capturado, `pointermove` e
  // `pointerup` continuam sendo entregues NESTE elemento mesmo com o dedo a
  // 200px de distância. O mesmo caminho de código atende mouse, toque e caneta,
  // e o clique simples é só um arraste de comprimento zero (o seek acontece já
  // no `pointerdown`) — por isso NÃO há mais `onClick` de seek: mantê-lo junto
  // faria o clique buscar duas vezes.
  const scrubRatioRef = useRef(null);
  const pointerIdRef = useRef(null);
  const captureElRef = useRef(null);
  const rafRef = useRef(0);
  const pendingSeekRef = useRef(null);

  const scrubbing = scrubRatio !== null;
  const canSeek = !disabled && isSeekable(duration);

  function setScrub(r) {
    scrubRatioRef.current = r;
    setScrubRatio(r);
  }

  function applyTime(t) {
    const a = audioRef.current;
    if (!a) return;
    // Atribuir `currentTime` levanta em elemento ainda não buscável; uma exceção
    // escapando de um handler de ponteiro deixaria a captura pendurada.
    try { a.currentTime = t; } catch (_) { return; }
    setCurrentTime(t);
  }

  // Busca AO VIVO durante o arraste (como WhatsApp/Telegram), com throttle de um
  // quadro: um arraste rápido geraria dezenas de seeks por segundo num Ogg e
  // engasgaria a decodificação. Com o rAF, no máximo um por frame.
  function scheduleSeek(t) {
    pendingSeekRef.current = t;
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      const v = pendingSeekRef.current;
      pendingSeekRef.current = null;
      if (v !== null) applyTime(v);
    });
  }

  function cancelScheduledSeek() {
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = 0; }
    pendingSeekRef.current = null;
  }

  function releaseCapture() {
    const el = captureElRef.current;
    const id = pointerIdRef.current;
    captureElRef.current = null;
    pointerIdRef.current = null;
    if (el && id !== null && el.hasPointerCapture && el.hasPointerCapture(id)) {
      try { el.releasePointerCapture(id); } catch (_) { /* já solto */ }
    }
  }

  // Mede sempre a BARRA de 4px, nunca o envelope de toque: os dois têm a mesma
  // caixa horizontal hoje, e depender disso seria uma armadilha silenciosa.
  function ratioAt(e) {
    const el = barRef.current;
    return el ? ratioFromPointer(e.clientX, el.getBoundingClientRect()) : 0;
  }

  function onScrubPointerDown(e) {
    if (!canSeek) return;
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    const el = e.currentTarget;
    try { el.setPointerCapture(e.pointerId); } catch (_) { /* segue sem captura */ }
    pointerIdRef.current = e.pointerId;
    captureElRef.current = el;
    const r = ratioAt(e);
    setScrub(r);
    applyTime(timeFromRatio(r, duration));
    // Sem isto o arraste vira seleção de texto da bolha e o foco cai aqui por
    // clique (o anel de foco passa a ser só do teclado, que é o que se quer).
    e.preventDefault();
    e.stopPropagation();
  }

  function onScrubPointerMove(e) {
    if (pointerIdRef.current === null || e.pointerId !== pointerIdRef.current) return;
    const r = ratioAt(e);
    setScrub(r);
    scheduleSeek(timeFromRatio(r, duration));
  }

  function onScrubPointerUp(e) {
    if (pointerIdRef.current === null || e.pointerId !== pointerIdRef.current) return;
    cancelScheduledSeek();
    applyTime(timeFromRatio(ratioAt(e), duration));
    releaseCapture();
    setScrub(null);
    e.stopPropagation();
  }

  function onScrubPointerCancel(e) {
    if (pointerIdRef.current === null || e.pointerId !== pointerIdRef.current) return;
    cancelScheduledSeek();
    // `pointercancel` não carrega coordenada confiável — vale a última do gesto.
    const last = scrubRatioRef.current;
    if (last !== null) applyTime(timeFromRatio(last, duration));
    releaseCapture();
    setScrub(null);
  }

  // F4 — segunda via para retroceder, sem depender de precisão de ponteiro.
  // Até aqui a barra era uma <div> sem role, sem tabIndex e sem ARIA: não havia
  // NENHUMA forma de mover a posição pelo teclado.
  function onScrubKeyDown(e) {
    if (!canSeek) return;
    let handled = true;
    switch (e.key) {
      case 'ArrowLeft':  applyTime(nudge(currentTime, -NUDGE_SECONDS, duration)); break;
      case 'ArrowRight': applyTime(nudge(currentTime, NUDGE_SECONDS, duration)); break;
      case 'Home':       applyTime(0); break;
      case 'End':        applyTime(duration); break;
      case ' ':
      case 'Spacebar':   togglePlay(); break;
      default: handled = false;
    }
    // preventDefault SÓ no que foi tratado: espaço e setas continuam rolando o
    // chat quando o foco não está na barra.
    if (handled) { e.preventDefault(); e.stopPropagation(); }
  }

  // Desmontar no meio do gesto (chegou mensagem, trocou de conversa) não pode
  // deixar ponteiro capturado nem quadro agendado.
  useEffect(() => () => { cancelScheduledSeek(); releaseCapture(); }, []);

  const progress = progressPercent({ currentTime, duration, scrubRatio });
  // G1 — o rótulo mostrava a DURAÇÃO sempre que o áudio estava pausado. Efeito
  // colateral cruel para este bug: retroceder com o áudio parado funcionava e o
  // número na tela não mudava, então o seek bem-sucedido parecia falho também.
  const shownTime = displayTime({ currentTime, duration, scrubRatio });

  // O envelope de toque abaixo merece explicação, e ela mora AQUI e não dentro
  // do template: uma crase dentro de html`...` FECHA o literal e derruba o
  // módulo inteiro em silêncio (o erro morre num console.warn do carregador).
  //
  // • A barra CONTINUA com 4px de altura visual. O alvo do ponteiro é que passa
  //   a ter ~20px, por padding vertical anulado por margem negativa — o layout
  //   não anda um pixel nas três superfícies que usam o player.
  // • Margem e touch-action inline de propósito: o Tailwind aqui é o runtime
  //   vendorizado e valor arbitrário exótico falha CALADO; um alvo de 0px de
  //   altura reintroduziria o bug sem nenhum sinal.
  // • Sem "relative" no envelope: ele avança 8px sobre o rótulo de tempo, e é o
  //   rótulo (irmão posterior, em fluxo) que precisa vencer o hit-test nessa
  //   sobreposição — senão o texto deixa de ser selecionável.
  // • touch-action:none é OBRIGATÓRIO: sem ele o navegador móvel lê o arraste
  //   horizontal como rolagem e ROUBA o gesto — o bug voltaria só no celular e
  //   passaria em todo teste de desktop.
  // • A transição de 100ms sai durante o arraste: interpolar faz a barra correr
  //   ATRÁS do dedo, o que lê como "não acompanhou".
  // • A bolinha fica opaca enquanto se arrasta: no toque não existe :hover e ela
  //   sumiria justamente durante o gesto.
  const scrubEnvelopeStyle = 'margin-top:-8px;margin-bottom:-8px;touch-action:none';

  return html`
    <div class="flex items-center gap-[8px] mb-1" style="min-width:240px">
      <audio ref=${audioRef} preload="metadata" src=${audioSrc}></audio>

      <!-- Play/Pause -->
      <button type="button" onClick=${togglePlay}
        class="w-[32px] h-[32px] flex items-center justify-center rounded-full shrink-0 text-wa-teal hover:text-wa-tealDark transition-colors">
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
        <div
          class="group py-[8px] select-none rounded-full outline-none focus:ring-2 focus:ring-wa-teal/50 ${canSeek ? 'cursor-pointer' : ''}"
          style=${scrubEnvelopeStyle}
          role="slider"
          tabIndex=${canSeek ? 0 : -1}
          aria-label="Posição do áudio"
          aria-orientation="horizontal"
          aria-valuemin="0"
          aria-valuemax=${canSeek ? Math.round(duration) : 0}
          aria-valuenow=${Math.round(shownTime)}
          aria-valuetext=${formatClock(shownTime)}
          aria-disabled=${canSeek ? null : 'true'}
          onKeyDown=${canSeek ? onScrubKeyDown : null}
          onPointerDown=${canSeek ? onScrubPointerDown : null}
          onPointerMove=${canSeek ? onScrubPointerMove : null}
          onPointerUp=${canSeek ? onScrubPointerUp : null}
          onPointerCancel=${canSeek ? onScrubPointerCancel : null}
          onClick=${canSeek ? ((e) => e.stopPropagation()) : null}
        >
          <div ref=${barRef} class="relative h-[4px] bg-wa-border rounded-full">
            <div class="absolute left-0 top-0 h-full bg-wa-teal rounded-full ${scrubbing ? '' : 'transition-[width] duration-100'}"
              style="width: ${progress}%"></div>
            <div class="absolute top-1/2 -translate-y-1/2 w-[12px] h-[12px] bg-wa-teal rounded-full transition-opacity ${scrubbing ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}"
              style="left: calc(${progress}% - 6px)"></div>
          </div>
        </div>
        <div class="flex justify-between gap-[8px]">
          <span class="text-[11px] text-wa-secondary">${formatClock(shownTime)}</span>
          <span class="text-[11px] text-wa-secondary">${formatClock(duration)}</span>
        </div>
      </div>

      <!-- Speed button -->
      <button type="button" onClick=${cycleSpeed}
        class="text-[11px] font-medium px-[6px] py-[2px] rounded-full shrink-0 transition-colors
          ${speed === 1 ? 'text-wa-secondary bg-wa-hover' : 'text-white bg-wa-teal'}">
        ${speed}x
      </button>
    </div>
  `;
}
