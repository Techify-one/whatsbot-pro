// Tabela de "Conversas" (ciclos) do atendimento, com COLUNAS DINÂMICAS + filtro.
//
// - Colunas: rótulos FIXOS (Início/Fim/Atendente/Observações) + uma por rótulo
//   EXTRA atual (scope=conversa), NA ORDEM da config.
// - Rótulos EXTRAS apagados NÃO aparecem: o backend só devolve em `c.fields` os
//   valores cujo rótulo ainda existe; aqui renderizamos apenas as defs recebidas.
//   (O dado do rótulo apagado segue no banco, recuperável só por lá.)
// - Filtro de colunas (botão "Colunas"): liga/desliga cada coluna; a escolha é
//   persistida por-dispositivo em localStorage (storageKey).
// Reutilizado pelo painel do chat, pelo modal de detalhe da lista e pelo HISTÓRICO de
// atendimentos (via startField/endField='opened_at'/'closed_at'). Cores wa-*.

import { h } from 'preact';
import { useState, useMemo } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

function fmtTs(ts) {
  if (!ts) return '—';
  try { return new Date(ts * 1000).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }); }
  catch (e) { return '—'; }
}

function lsGet(k) { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } }

const BASE = [
  { key: '__inicio', label: 'Início', base: true },
  { key: '__fim', label: 'Fim', base: true },
  { key: '__atendente', label: 'Atendente', base: true },
  { key: '__obs', label: 'Observações', base: true },
];

export function ConversasTable({ conversas = [], fieldDefs = [], storageKey = 'whatsbot_atend_cols',
                                defaultHidden = {}, showFilter = true,
                                startField = 'started_at', endField = 'ended_at',
                                onRowClick = null,
                                emptyText = 'Nenhuma conversa vinculada ainda.' }) {
  // Conjunto de colunas: fixos (base) + rótulos EXTRAS atuais (na ordem da config).
  // Defs fixas que cheguem em fieldDefs são ignoradas (já cobertas pela base).
  const cols = useMemo(() => {
    const out = [...BASE];
    const seen = new Set();
    for (const d of (fieldDefs || [])) {
      if (d && d.key && !d.fixed && !seen.has(d.key)) {
        seen.add(d.key);
        out.push({ key: d.key, label: d.label || d.key, type: d.type });
      }
    }
    return out;
  }, [fieldDefs]);

  // Visibilidade persistida (mapa colKey -> true = oculta). Default configurável.
  const [hidden, setHidden] = useState(() => lsGet(storageKey) || { ...defaultHidden });
  const [open, setOpen] = useState(false);
  const visible = (k) => !hidden[k];
  function toggle(k) {
    setHidden((prev) => {
      const n = { ...prev };
      if (n[k]) delete n[k]; else n[k] = true;
      lsSet(storageKey, n);
      return n;
    });
  }

  const visCols = cols.filter((c) => visible(c.key));

  function cell(c, col) {
    if (col.key === '__inicio') return fmtTs(c[startField]);
    if (col.key === '__fim') return fmtTs(c[endField]);
    if (col.key === '__atendente') return c.assignee_name || '—';
    if (col.key === '__obs') return c.obs || '—';
    const v = (c.fields || {})[col.key];
    if (col.type === 'checkbox') return v === true ? 'Sim' : (v === false ? 'Não' : '—');
    return (v === null || v === undefined || v === '') ? '—' : String(v);
  }

  return html`
    <div>
      ${showFilter ? html`
        <div class="relative flex justify-end mb-1">
          <button onClick=${() => setOpen((o) => !o)}
            class="text-[12px] text-wa-secondary hover:text-wa-text px-2 py-1 rounded hover:bg-wa-hover">
            Colunas ▾
          </button>
          ${open ? html`
            <div class="absolute right-0 top-full z-20 mt-1 w-52 max-h-64 overflow-auto bg-wa-bg border border-wa-border rounded-lg shadow-xl p-2"
              onMouseLeave=${() => setOpen(false)}>
              <div class="text-[11px] text-wa-secondary px-1 pb-1">Mostrar colunas</div>
              ${cols.map((col) => html`
                <label key=${col.key}
                  class="flex items-center gap-2 px-1 py-1 text-[13px] text-wa-text cursor-pointer hover:bg-wa-hover rounded">
                  <input type="checkbox" checked=${visible(col.key)} onChange=${() => toggle(col.key)} />
                  <span class="truncate">${col.label}</span>
                </label>`)}
            </div>` : null}
        </div>` : null}

      ${(conversas || []).length === 0
        ? html`<div class="text-[12px] text-wa-secondary">${emptyText}</div>`
        : visCols.length === 0
        ? html`<div class="text-[12px] text-wa-secondary">Nenhuma coluna selecionada.</div>`
        : html`
        <div class="overflow-x-auto">
          <table class="w-full text-[12px]">
            <thead><tr class="text-wa-secondary text-left">
              ${visCols.map((col) => html`<th key=${col.key} class="py-1 pr-2 whitespace-nowrap">${col.label}</th>`)}
            </tr></thead>
            <tbody>
              ${conversas.map((c) => html`<tr key=${c.id}
                onClick=${onRowClick ? () => onRowClick(c) : undefined}
                title=${onRowClick ? 'Abrir esta conversa' : undefined}
                class="border-t border-wa-border text-wa-text align-top ${onRowClick ? 'cursor-pointer hover:bg-wa-hover' : ''}">
                ${visCols.map((col) => html`<td key=${col.key}
                  class="py-1 pr-2 ${(col.key === '__inicio' || col.key === '__fim') ? 'whitespace-nowrap' : ''}">${cell(c, col)}</td>`)}
              </tr>`)}
            </tbody>
          </table>
        </div>`}
    </div>`;
}

export default ConversasTable;
