/**
 * plano 63 F2 — shim fino sobre o motor unificado (`soundEngine`).
 *
 * Antes: criava um `new AudioContext()` POR disparo (vazamento) e tocava a sirene
 * com gain FIXO 0.3 (ignorava qualquer preferência). Agora delega ao motor, que
 * usa um AudioContext singleton e resolve som/volume/duração pelas 3 camadas.
 *
 * Os DOIS call sites reais (transferência IA→humano e atribuição entre atendentes)
 * chamam `soundEngine.playEvent('ia_to_human' | 'assigned_to_me', ...)` direto —
 * cada um tem sua própria preferência. Este shim (padrão: evento IA→humano) fica
 * só para chamadores legados que importem `playTransferAlert`.
 */
import { playEvent } from './soundEngine.js';

/**
 * @param {number} [seconds=5] - Duração do alerta em segundos.
 */
export function playTransferAlert(seconds = 5) {
  playEvent('ia_to_human', { durationOverride: seconds });
}
