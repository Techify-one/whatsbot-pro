// Tabela de "Atendimentos" (ciclos) do protocolo, com COLUNAS DINÂMICAS + filtro.
//
// Colunas: fixos (Início/Fim/Atendente) + rótulos do plugin (escopo atendimento, lidos de
// `c.fields`, incluindo Observações), NA ORDEM da config. (Os atributos personalizados de
// CONVERSA do core não fazem mais parte do plugin.)
// Rótulos apagados NÃO aparecem: o backend só devolve em `c.fields` os valores cujo rótulo
// ainda existe; aqui renderizamos só as defs recebidas.
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

// Grupo único de colunas: info fixa + rótulos da atendimento.
const GROUPS = [
  { id: 'atendimento', label: 'Informações do atendimento' },
];

const BASE = [
  { key: '__inicio', label: 'Início', base: true, group: 'atendimento' },
  { key: '__fim', label: 'Fim', base: true, group: 'atendimento' },
  { key: '__atendente', label: 'Atendente', base: true, group: 'atendimento' },
  { key: '__aberto', label: 'Aberto por', base: true, group: 'atendimento' },
  { key: '__fechado', label: 'Fechado por', base: true, group: 'atendimento' },
];

export function AtendimentosTable({ atendimentos = [], fieldDefs = [],
                                storageKey = 'whatsbot_proto_cols',
                                defaultHidden = {}, showFilter = true,
                                startField = 'started_at', endField = 'ended_at',
                                onRowClick = null,
                                emptyText = 'Nenhum atendimento vinculado ainda.' }) {
  // Conjunto de colunas: fixos (Início/Fim/Atendente) + rótulos do plugin (obs incluso,
  // lidos de c.fields). O tipo "atendente" NÃO vira coluna aqui (a base __atendente já mostra
  // o atendente do ciclo; o rótulo atendente é só de entrada).
  const cols = useMemo(() => {
    const out = [...BASE];
    const seen = new Set();
    for (const d of (fieldDefs || [])) {
      if (d && d.key && !d.fixed && d.type !== 'atendente' && !seen.has(d.key)) {
        seen.add(d.key);
        out.push({ key: d.key, label: d.label || d.key, type: d.type, group: 'atendimento', src: 'fields' });
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
  // Grupos visíveis (só os com ≥1 coluna). Com grupo único não há linha de cabeçalho de grupo.
  const visGroups = GROUPS
    .map((g) => ({ ...g, cols: visCols.filter((c) => c.group === g.id) }))
    .filter((g) => g.cols.length);
  const showGroupHeader = visGroups.length > 1;
  // Colunas achatadas na ordem dos grupos (grupo único ⇒ sem separador visual).
  const flatCols = visGroups.flatMap((g) => g.cols.map((c) => ({ ...c, _sep: false })));

  function cell(c, col) {
    if (col.key === '__inicio') return fmtTs(c[startField]);
    if (col.key === '__fim') return fmtTs(c[endField]);
    if (col.key === '__atendente') return c.assignee_name || '—';
    // "Aberto por" = quem abriu o ciclo (opened_by_name: Contato/IA/atendente).
    if (col.key === '__aberto') return c.opened_by_name || '—';
    // "Fechado por" = o atendente salvo, só quando o ciclo já foi encerrado (ended_at).
    if (col.key === '__fechado') return c[endField] ? (c.assignee_name || '—') : '—';
    const v = (c.fields || {})[col.key];
    if (col.type === 'checkbox') return v === true ? 'Sim' : (v === false ? 'Não' : '—');
    return (v === null || v === undefined || v === '') ? '—' : String(v);
  }

  const groupCls = () => 'text-wa-teal';

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
                  ${visGroups.map((g) => html`<th key=${g.id} colspan=${g.cols.length}
                    class="py-1 pr-2 text-[10px] uppercase tracking-wide font-semibold ${groupCls(g.id)}">${g.label}</th>`)}
                </tr>` : null}
              <tr class="text-wa-secondary text-left">
                ${flatCols.map((col) => html`<th key=${col.key}
                  class="py-1 pr-2 whitespace-nowrap ${col._sep ? 'border-l border-wa-border pl-2' : ''}">${col.label}</th>`)}
              </tr>
            </thead>
            <tbody>
              ${atendimentos.map((c) => html`<tr key=${c.id}
                onClick=${onRowClick ? () => onRowClick(c) : undefined}
                title=${onRowClick ? 'Abrir este atendimento' : undefined}
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
