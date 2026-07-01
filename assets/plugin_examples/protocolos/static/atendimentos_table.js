// Tabela de "Atendimentos" (ciclos) do protocolo, com COLUNAS DINÂMICAS + filtro.
//
// Colunas em DOIS grupos, com cabeçalho de grupo p/ deixar CLARO a origem de cada coluna:
//  - "Informações da atendimento": fixos (Início/Fim/Atendente/Observações) + rótulos do
//    plugin (escopo atendimento, lidos de `c.fields`), NA ORDEM da config.
//  - "Atributos personalizados": atributos do core (is_system=0, lidos de `c.attrs`).
// Rótulos/atributos apagados NÃO aparecem: o backend só devolve em `c.fields`/`c.attrs` os
// valores cujo rótulo/atributo ainda existe; aqui renderizamos só as defs recebidas.
// Filtro de colunas (botão "Colunas") liga/desliga cada coluna; a escolha é persistida
// por-dispositivo em localStorage (storageKey). Cores wa-*.

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

// Grupos de colunas (origem clara). 'atendimento' = info + rótulos da atendimento; 'atributo' =
// atributo personalizado do core. A ordem aqui é a ordem dos grupos na tabela.
const GROUPS = [
  { id: 'atendimento', label: 'Informações da atendimento' },
  { id: 'atributo', label: 'Atributos personalizados' },
];

const BASE = [
  { key: '__inicio', label: 'Início', base: true, group: 'atendimento' },
  { key: '__fim', label: 'Fim', base: true, group: 'atendimento' },
  { key: '__atendente', label: 'Atendente', base: true, group: 'atendimento' },
  { key: '__obs', label: 'Observações', base: true, group: 'atendimento' },
];

export function AtendimentosTable({ atendimentos = [], fieldDefs = [], attrDefs = [],
                                storageKey = 'whatsbot_proto_cols',
                                defaultHidden = {}, showFilter = true,
                                startField = 'started_at', endField = 'ended_at',
                                onRowClick = null,
                                emptyText = 'Nenhuma atendimento vinculada ainda.' }) {
  // Conjunto de colunas: fixos (group 'atendimento') + rótulos EXTRAS do plugin (group
  // 'atendimento', lidos de c.fields) + atributos do core (group 'atributo', lidos de
  // c.attrs). Defs fixas que cheguem em fieldDefs são ignoradas (já cobertas pela base).
  const cols = useMemo(() => {
    const out = [...BASE];
    const seen = new Set();
    for (const d of (fieldDefs || [])) {
      if (d && d.key && !d.fixed && !seen.has(d.key)) {
        seen.add(d.key);
        out.push({ key: d.key, label: d.label || d.key, type: d.type, group: 'atendimento', src: 'fields' });
      }
    }
    for (const d of (attrDefs || [])) {
      const ck = `attr:${d && d.key}`;
      if (d && d.key && !seen.has(ck)) {
        seen.add(ck);
        out.push({ key: ck, attrKey: d.key, label: d.label || d.key, type: d.type, group: 'atributo', src: 'attrs' });
      }
    }
    return out;
  }, [fieldDefs, attrDefs]);

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
  // Grupos visíveis (ordem fixa, só os com ≥1 coluna). Com >1 grupo, o cabeçalho ganha
  // uma linha de grupo (Informações da atendimento | Atributos personalizados).
  const visGroups = GROUPS
    .map((g) => ({ ...g, cols: visCols.filter((c) => c.group === g.id) }))
    .filter((g) => g.cols.length);
  const showGroupHeader = visGroups.length > 1;
  // Colunas achatadas na ordem dos grupos; marca a 1ª de cada grupo (≠ 1º) p/ um separador
  // visual (borda à esquerda) entre "atendimento" e "atributos".
  const flatCols = visGroups.flatMap((g, gi) => g.cols.map((c, ci) => ({ ...c, _sep: gi > 0 && ci === 0 })));

  function cell(c, col) {
    if (col.key === '__inicio') return fmtTs(c[startField]);
    if (col.key === '__fim') return fmtTs(c[endField]);
    if (col.key === '__atendente') return c.assignee_name || '—';
    if (col.key === '__obs') return c.obs || '—';
    const src = col.src === 'attrs' ? (c.attrs || {}) : (c.fields || {});
    const v = src[col.attrKey || col.key];
    if (col.type === 'checkbox') return v === true ? 'Sim' : (v === false ? 'Não' : '—');
    return (v === null || v === undefined || v === '') ? '—' : String(v);
  }

  const groupCls = (gid) => (gid === 'atributo' ? 'text-amber-600' : 'text-wa-teal');

  return html`
    <div>
      ${showFilter ? html`
        <div class="relative flex justify-end mb-1">
          <button onClick=${() => setOpen((o) => !o)}
            class="text-[12px] text-wa-secondary hover:text-wa-text px-2 py-1 rounded hover:bg-wa-hover">
            Colunas ▾
          </button>
          ${open ? html`
            <div class="absolute right-0 top-full z-20 mt-1 w-56 max-h-72 overflow-auto bg-wa-bg border border-wa-border rounded-lg shadow-xl p-2"
              onMouseLeave=${() => setOpen(false)}>
              ${GROUPS.map((g) => {
                const gcols = cols.filter((c) => c.group === g.id);
                if (!gcols.length) return null;
                return html`<div key=${g.id}>
                  <div class="text-[10px] uppercase tracking-wide px-1 pt-1.5 pb-0.5 font-semibold ${groupCls(g.id)}">${g.label}</div>
                  ${gcols.map((col) => html`
                    <label key=${col.key}
                      class="flex items-center gap-2 px-1 py-1 text-[13px] text-wa-text cursor-pointer hover:bg-wa-hover rounded">
                      <input type="checkbox" checked=${visible(col.key)} onChange=${() => toggle(col.key)} />
                      <span class="truncate">${col.label}</span>
                    </label>`)}
                </div>`;
              })}
            </div>` : null}
        </div>` : null}

      ${(atendimentos || []).length === 0
        ? html`<div class="text-[12px] text-wa-secondary">${emptyText}</div>`
        : flatCols.length === 0
        ? html`<div class="text-[12px] text-wa-secondary">Nenhuma coluna selecionada.</div>`
        : html`
        <div class="overflow-x-auto">
          <table class="w-full text-[12px]">
            <thead>
              ${showGroupHeader ? html`
                <tr class="text-left">
                  ${visGroups.map((g, gi) => html`<th key=${g.id} colspan=${g.cols.length}
                    class="py-1 pr-2 text-[10px] uppercase tracking-wide font-semibold ${gi > 0 ? 'border-l border-wa-border pl-2' : ''} ${groupCls(g.id)}">${g.label}</th>`)}
                </tr>` : null}
              <tr class="text-wa-secondary text-left">
                ${flatCols.map((col) => html`<th key=${col.key}
                  class="py-1 pr-2 whitespace-nowrap ${col._sep ? 'border-l border-wa-border pl-2' : ''}">${col.label}</th>`)}
              </tr>
            </thead>
            <tbody>
              ${atendimentos.map((c) => html`<tr key=${c.id}
                onClick=${onRowClick ? () => onRowClick(c) : undefined}
                title=${onRowClick ? 'Abrir esta atendimento' : undefined}
                class="border-t border-wa-border text-wa-text align-top ${onRowClick ? 'cursor-pointer hover:bg-wa-hover' : ''}">
                ${flatCols.map((col) => html`<td key=${col.key}
                  class="py-1 pr-2 ${col._sep ? 'border-l border-wa-border pl-2' : ''} ${(col.key === '__inicio' || col.key === '__fim') ? 'whitespace-nowrap' : ''}">${cell(c, col)}</td>`)}
              </tr>`)}
            </tbody>
          </table>
        </div>`}
    </div>`;
}

export default AtendimentosTable;
