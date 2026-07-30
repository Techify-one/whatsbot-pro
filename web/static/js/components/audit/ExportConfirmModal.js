// Aviso de confirmação antes de exportar a trilha de auditoria.
// O backend exporta no máximo `cap` linhas por arquivo (_EXPORT_CAP em
// server/routes/audit.py) — em vez de o operador descobrir isso só depois,
// pelo arquivo cortado, o teto é dito ANTES, junto de quantas linhas os
// filtros aplicados devolvem hoje. Continuar é decisão dele.

import { h } from 'preact';
import { useEffect } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

const FORMAT_LABEL = { csv: 'CSV', json: 'JSON' };

export function ExportConfirmModal({ format, total, cap, busy, onConfirm, onCancel }) {
  // ESC fecha (o modal é destrutivo-zero, então cancelar é o caminho barato).
  useEffect(() => {
    if (!format) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [format, onCancel]);

  if (!format) return null;

  const label = FORMAT_LABEL[format] || String(format).toUpperCase();
  // `total` é a contagem dos MESMOS filtros aplicados na lista — é exatamente o
  // universo que o export vai varrer, então dá pra dizer se vai truncar.
  const willTruncate = Number.isFinite(total) && total > cap;
  const fmt = (n) => Number(n).toLocaleString('pt-BR');

  return html`
    <div
      class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-md w-full p-6 relative" role="alertdialog" aria-modal="true">
        <button
          onClick=${onCancel}
          class="absolute top-3 right-3 text-wa-secondary hover:text-wa-text transition-colors p-1 rounded"
          title="Fechar"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>

        <div class="flex items-start gap-3 mb-3 pr-6">
          <span class="text-[22px] leading-none shrink-0">${willTruncate ? '⚠️' : '📄'}</span>
          <h2 class="text-base font-semibold text-wa-text">Exportar auditoria em ${label}</h2>
        </div>

        <p class="text-sm text-wa-text mb-3">
          A exportação tem um limite de <strong>${fmt(cap)} linhas</strong> por arquivo.
        </p>

        ${willTruncate ? html`
          <p class="text-[13px] text-amber-700 bg-amber-500/10 border border-amber-500/30 rounded-lg py-2 px-3 mb-3">
            Os filtros atuais têm <strong>${fmt(total)} registros</strong>. O arquivo trará
            apenas os <strong>${fmt(cap)} mais recentes</strong> — o restante fica de fora.
            Para levar tudo, refine o período (campos “De”/“Até”) e exporte em partes.
          </p>
        ` : html`
          <p class="text-[13px] text-wa-secondary bg-wa-panel border border-wa-border rounded-lg py-2 px-3 mb-3">
            Os filtros atuais têm <strong>${fmt(total)} ${total === 1 ? 'registro' : 'registros'}</strong>,
            então nada será cortado.
          </p>
        `}

        <p class="text-[13px] text-wa-secondary">
          A exportação usa os filtros aplicados na tela e fica registrada na própria trilha.
        </p>

        <div class="flex justify-end gap-2 mt-5">
          <button
            onClick=${onCancel}
            class="px-4 py-2 rounded-lg text-sm text-wa-text hover:bg-wa-hover transition-colors cursor-pointer"
          >
            Cancelar
          </button>
          <button
            onClick=${onConfirm}
            disabled=${busy}
            class="bg-wa-teal text-white text-sm font-medium rounded-lg py-2 px-4 hover:opacity-90 transition-opacity cursor-pointer disabled:opacity-50"
          >
            ${busy ? 'Exportando…' : `Exportar ${label}`}
          </button>
        </div>
      </div>
    </div>
  `;
}
