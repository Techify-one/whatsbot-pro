// Override da aba "Protocolos". Aba dedicada à entidade PROTOCOLO:
//  - Lista (colunas ordenáveis por clique) OU Kanban (agrupado por Status ou Atendente,
//    com drag-and-drop que ALTERA o estado: soltar numa coluna fecha/reabre ou atribui).
//  - Filtros: status, busca por cliente e PERÍODO de criação (intervalo + presets).
//  - Abrir um protocolo → detalhe; clicar numa de suas atendimentos → vai EXATAMENTE
//    àquele ponto da atendimento no chat (?message=<_id>).
// Desativar o plugin → a aba volta 100% ao Protocolos nativo.

import { h } from 'preact';
import { useState, useEffect, useCallback, useMemo, useRef } from 'preact/hooks';
import htm from 'htm';
import { AtendimentosTable } from '/plugins/protocolos/static/atendimentos_table.js';
import { ResolveForm, LabeledField } from '/plugins/protocolos/static/resolve_form.js';
// Semeadura dos campos do protocolo — módulo PURO (testes em proto_fields.test.js).
import { seedProtocolValues, mergeSeed, isMultiDef } from '/plugins/protocolos/static/proto_fields.js';
import { OptionListSelect } from '/static/js/components/OptionListSelect.js';
import { useInfiniteScroll } from '/static/js/hooks/useInfiniteScroll.js';

const html = htm.bind(h);

// Tamanho da página — vale para a Lista E para cada coluna do Kanban (que pagina
// sozinha contra /grouped/column; o agrupamento é feito no servidor).
const PAGE_SIZE = 50;
// Distância do fim (px) em que já pedimos a próxima página.
const NEAR_BOTTOM_PX = 300;

// Ancestral que REALMENTE rola. A tela do plugin pode rolar num container interno em vez
// da janela, e é por isso que um IntersectionObserver ancorado na viewport não dispara —
// preferimos escutar o scroll de quem rola de fato. `null` = a própria página.
function scrollParentOf(el) {
  let n = el && el.parentElement;
  while (n) {
    const s = window.getComputedStyle(n);
    if (/(auto|scroll|overlay)/.test(s.overflowY) && n.scrollHeight > n.clientHeight) return n;
    n = n.parentElement;
  }
  return null;
}

// True quando o scroller está a menos de NEAR_BOTTOM_PX do fim. `sc` null = página.
function nearBottom(sc) {
  const el = sc || document.scrollingElement || document.documentElement;
  return el.scrollTop + el.clientHeight >= el.scrollHeight - NEAR_BOTTOM_PX;
}

const MODE_KEY = 'whatsbot_protocolos_mode';    // 'lista' | 'kanban'
const KGROUP_KEY = 'whatsbot_protocolos_kgroup'; // legado: 'status' | 'atendente' (migrado p/ VIEW_KEY)
const VIEW_KEY = 'whatsbot_protocolos_view';     // id (numérico) da aba ativa; sentinelas legadas '__status'/'__atendente' caem na 1ª aba
function lsGet(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }

// Deep-link da aba ativa do Kanban (Plano 24) — ?view=<id>. Padrão MANUAL do plugin
// (URLSearchParams + replaceState), como o ?detail= já faz; NÃO usa os hooks do core
// (evita divergência pelo boundary de plugin). Convivem: escrever ?view= preserva o
// ?detail= corrente e vice-versa.
// Copia texto p/ o clipboard funcionando também em HTTP/contexto não-seguro (o painel
// costuma rodar num IP de LAN sem TLS, onde navigator.clipboard não existe) — fallback
// via execCommand num textarea temporário. Chama onOk() no sucesso.
function copyText(text, onOk) {
  const fallback = () => {
    try {
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.focus(); ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok && onOk) onOk();
    } catch (e) { /* clipboard indisponível */ }
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => onOk && onOk()).catch(fallback);
  } else { fallback(); }
}

function readUrlParam(key) { try { return new URLSearchParams(location.search).get(key); } catch (e) { return null; } }
function writeUrlParam(key, value) {
  try {
    const p = new URLSearchParams(location.search);
    if (value == null || value === '') p.delete(key); else p.set(key, String(value));
    const qs = p.toString();
    history.replaceState(null, '', `${location.pathname}${qs ? `?${qs}` : ''}`);
  } catch (e) { /* ignore */ }
}

// Fallback SÓ de render: garante que o board nunca fique em branco quando não há nenhuma
// visualização (ex.: todas excluídas). NÃO é uma aba da barra — as abas Status/Atendente
// agora são views REAIS (seed 010), editáveis/excluíveis como qualquer visualização criada
// pela interface.
const FALLBACK_VIEW = { id: '__fallback', group_by: 'status', filters: {} };

function fmtTs(ts) {
  if (!ts) return '—';
  try { return new Date(ts * 1000).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }); }
  catch (e) { return '—'; }
}

function fmtCell(v, def) {
  if (def.type === 'checkbox') return v ? 'Sim' : 'Não';
  if (isMultiDef(def)) return (Array.isArray(v) && v.length) ? v.join(', ') : '—';
  return (v == null || v === '') ? '—' : String(v);
}

// Campo do protocolo "preenchido"? checkbox sempre conta como preenchido (consistente
// com o backend _missing_required e com o isFilled do popup de resolver). Usado pelo gate
// de obrigatórios do detalhe e pelo guard do drag para "Fechado".
function isFilledProto(def, v) {
  if (def.type === 'checkbox') return true;
  if (isMultiDef(def)) return Array.isArray(v) ? v.length > 0 : !!v;
  return String(v == null ? '' : v).trim() !== '';
}

// yyyy-mm-dd local (para os <input type=date>) e conversão p/ epoch (segundos).
function ymd(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}
function dayStartEpoch(s) { if (!s) return null; const d = new Date(`${s}T00:00:00`); return Number.isNaN(d) ? null : Math.floor(d.getTime() / 1000); }
function dayEndEpoch(s) { if (!s) return null; const d = new Date(`${s}T23:59:59`); return Number.isNaN(d) ? null : Math.floor(d.getTime() / 1000); }

// Abre EXATAMENTE a atendimento (thread) no hub de chat — mesmo primitivo do kanban
// nativo (history.pushState + popstate). Com `messageId`, usa o permalink de mensagem
// (?message=<_id>) p/ rolar ATÉ aquele ponto da atendimento (estilo busca).
function openConversation(conversationId, messageId = null) {
  if (conversationId == null) return;
  const qs = messageId != null ? `?message=${messageId}` : '';
  history.pushState(null, '', `/conversations/${conversationId}${qs}`);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

// ── Filtro de período (intervalo + presets, estilo da referência) ─────────────
const DATE_PRESETS = [['Hoje', 0], ['7 dias', 7], ['30 dias', 30], ['90 dias', 90], ['1 ano', 365]];

function DateFilter({ from, to, active, onManual, onPreset, onClear }) {
  return html`
    <div class="flex flex-wrap items-center gap-2 mb-3 p-3 rounded-lg bg-wa-panel border border-wa-border">
      <span class="text-[12px] text-wa-secondary font-medium">Período (criação)</span>
      <button onClick=${onClear} class="text-[12px] text-wa-teal hover:underline">limpar</button>
      <div class="flex items-center gap-1.5">
        <input type="date" class="wa-field px-2 py-1.5 rounded-md text-[13px]" value=${from}
          onInput=${(e) => onManual(e.target.value, to)} />
        <span class="text-wa-secondary">→</span>
        <input type="date" class="wa-field px-2 py-1.5 rounded-md text-[13px]" value=${to}
          onInput=${(e) => onManual(from, e.target.value)} />
      </div>
      <div class="flex flex-wrap gap-1.5">
        ${DATE_PRESETS.map(([lbl, d]) => html`<button key=${lbl} onClick=${() => onPreset(d, lbl)}
          class="px-2.5 py-1 rounded-md text-[12px] border ${active === lbl ? 'bg-wa-teal text-white border-wa-teal' : 'border-wa-border text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
        <button onClick=${onClear}
          class="px-2.5 py-1 rounded-md text-[12px] border ${active === 'tudo' ? 'bg-wa-teal text-white border-wa-teal' : 'border-wa-border text-wa-text hover:bg-wa-hover'}">Tudo</button>
      </div>
    </div>`;
}

// ── Agrupamento do Kanban (uniforme: columns / columnIdOf / onDrop / confirmText) ──
// Espelha o padrão de web/static/js/components/attendances/grouping.js. `onDrop` ausente
// = modo SÓ-LEITURA (data): cards não arrastáveis. `confirmText` descreve EXATAMENTE a
// ação para o popup de confirmação (ver applyDrop).
function _startOfDayEpoch(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); return Math.floor(x.getTime() / 1000); }
function _ymdLabel(s) { try { return new Date(`${s}T00:00:00`).toLocaleDateString('pt-BR'); } catch (e) { return s; } }
function _monthLabel(ym) {
  try { const [y, m] = ym.split('-'); return new Date(+y, +m - 1, 1).toLocaleDateString('pt-BR', { month: 'short', year: 'numeric' }); }
  catch (e) { return ym; }
}
// Semana inicia na SEGUNDA-FEIRA (ISO). A chave da semana é o 'YYYY-MM-DD' da segunda (ordena
// como string). Rótulo: "Semana de DD/MM/AAAA".
function _weekStartYmd(epochSec) {
  const d = new Date(epochSec * 1000);
  d.setHours(0, 0, 0, 0);
  const dow = (d.getDay() + 6) % 7;  // Seg=0 … Dom=6
  d.setDate(d.getDate() - dow);
  return ymd(d);
}
function _weekLabel(wk) {
  try { return `Semana de ${new Date(`${wk}T00:00:00`).toLocaleDateString('pt-BR')}`; }
  catch (e) { return wk; }
}

// Tipos de campo de protocolo com OPÇÕES fixas (viram colunas de Kanban / filtro dropdown).
const OPTION_TYPES = new Set(['select', 'radio', 'checkboxes']);
// Um campo é MULTI-valor (várias colunas) quando select/checkboxes com `multiple`. (checkboxes
// SEM multiple guarda lista de 1 item — conta como valor único p/ agrupar.)
const isMultiField = (d) => (d.type === 'select' || d.type === 'checkboxes') && !!d.multiple;
// Campos de opção de um escopo, com {scope,key,label,type,options,multiple}. singleOnly → só
// valor único (agrupar/arrastar); senão todos (filtros).
function optionFields(defs, scope, singleOnly) {
  return (defs || [])
    .filter((d) => OPTION_TYPES.has(d.type) && Array.isArray(d.options) && d.options.length
                   && (!singleOnly || !isMultiField(d)))
    .map((d) => ({ scope, key: d.key, label: d.label || d.key, type: d.type,
                   options: d.options, multiple: !!d.multiple }));
}
// Campos de um escopo como FILTROS — QUALQUER tipo (não só opção). Opção → dropdown (options);
// os demais (texto/número/data/…) → campo de digitação (busca parcial). A restrição a tipo de
// opção fica SÓ no AGRUPAMENTO (optionFields), não nos filtros.
// Desdobra opções unidas por vírgula/quebra-de-linha numa MESMA entrada (rótulos legados
// digitados numa linha só, ex.: ['teste 1,teste 2, teste 3'] → ['teste 1','teste 2','teste 3']).
// Trim + sem vazios + sem duplicar, preservando a ordem. Um filtro de opção só é útil com as
// opções individualizadas (senão vira uma "opção" gigante com vírgulas). Consequência: uma
// opção de campo não pode conter vírgula — ela seria dividida em opções separadas.
function splitOptionList(options) {
  const out = [];
  const seen = new Set();
  for (const o of (options || [])) {
    for (const part of String(o).split(/[\n,]/)) {
      const p = part.trim();
      if (p && !seen.has(p)) { seen.add(p); out.push(p); }
    }
  }
  return out;
}

// Normaliza o valor de um filtro para LISTA (modo múltiplo do OptionListSelect). Tolera valores
// legados salvos como string única (view antiga) → vira lista de 1 item; vazio → [].
function asFilterList(v) {
  return Array.isArray(v) ? v : (v != null && v !== '' ? [v] : []);
}

function filterableFields(defs, scope) {
  return (defs || [])
    .filter((d) => d && d.key)
    .map((d) => ({ scope, key: d.key, label: d.label || d.key, type: d.type,
                   options: (OPTION_TYPES.has(d.type) && Array.isArray(d.options)) ? splitOptionList(d.options) : [],
                   multiple: !!d.multiple }));
}

// Fonte ÚNICA de categorias de filtro (ordem + rótulos + partição por escopo) — reusada pela
// barra de filtros ao vivo (ProtocolosList) E pelo painel "Configurar filtros da aba"
// (ViewGroupingConfig), p/ as duas nunca divergirem. `nativas` não traz campos (a barra usa
// widgets próprios; o painel usa a lista fixa de chaves status/atendente/q/periodo/canal);
// as demais trazem `fields` (campos de protocolo/atendimento) ou `attrs` (atributos de contato).
// "Outros" é defensivo: captura escopos de campo desconhecidos p/ nada sumir.
function filterCategories(filterFields, contactAttrDefs) {
  const ff = filterFields || [];
  return [
    { key: 'nativas',      label: 'Nativas' },
    { key: 'protocolos',   label: 'Protocolos',   fields: ff.filter((d) => d.scope === 'protocolo') },
    { key: 'atendimentos', label: 'Atendimentos', fields: ff.filter((d) => d.scope === 'atendimento') },
    { key: 'contato',      label: 'Contato',      attrs:  (contactAttrDefs || []) },
    { key: 'outros',       label: 'Outros',       fields: ff.filter((d) => d.scope !== 'protocolo' && d.scope !== 'atendimento') },
  ];
}

function buildGrouping(view, { users, groupFields, rows, apiPost }) {
  const gb = (view && view.group_by) || 'status';
  const cliente = (r) => r.contact_name || r.contact_phone || 'cliente';

  if (gb === 'atendente') {
    const nameOf = (col) => ((users.find((u) => `u:${u.id}` === col) || {}).name) || 'este atendente';
    return {
      columns: [{ id: '__none__', label: 'Não atribuído' },
                ...users.map((u) => ({ id: `u:${u.id}`, label: u.name || `Usuário #${u.id}` }))],
      columnIdOf: (r) => (r.assignee_user_id != null ? `u:${r.assignee_user_id}` : '__none__'),
      confirmText: (r, col) => (col === '__none__'
        ? `Remover a atribuição do protocolo de ${cliente(r)}?`
        : `Atribuir o protocolo de ${cliente(r)} a ${nameOf(col)}?`),
      onDrop: (r, col) => apiPost(`/protocolos/${r.id}/assign`, {
        assignee_user_id: col === '__none__' ? null : +col.slice(2),
        assignee_name: col === '__none__' ? '' : nameOf(col),
      }),
    };
  }

  if (gb === 'data') {
    const mode = (view && view.group_date_mode) || 'faixas';
    if (mode === 'faixas') {
      const now = new Date();
      const sToday = _startOfDayEpoch(now);
      const sYest = sToday - 86400;
      const sWeek = sToday - 6 * 86400;
      const sMonth = _startOfDayEpoch(new Date(now.getFullYear(), now.getMonth(), 1));
      return {
        readOnly: true,
        columns: [{ id: 'today', label: 'Hoje' }, { id: 'yesterday', label: 'Ontem' },
                  { id: 'week', label: 'Últimos 7 dias' }, { id: 'month', label: 'Este mês' },
                  { id: 'older', label: 'Mais antigos' }],
        columnIdOf: (r) => {
          const t = r.opened_at || 0;
          if (t >= sToday) return 'today';
          if (t >= sYest) return 'yesterday';
          if (t >= sWeek) return 'week';
          if (t >= sMonth) return 'month';
          return 'older';
        },
      };
    }
    const dayOf = (r) => (r.opened_at ? ymd(new Date(r.opened_at * 1000)) : null);
    const monOf = (r) => { if (!r.opened_at) return null; const d = new Date(r.opened_at * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; };
    const weekOf = (r) => (r.opened_at ? _weekStartYmd(r.opened_at) : null);
    const keyerFor = (g) => (g === 'mes' ? monOf : (g === 'semana' ? weekOf : dayOf));
    const labelerFor = (g) => (g === 'mes' ? _monthLabel : (g === 'semana' ? _weekLabel : _ymdLabel));
    const prefixFor = (g) => (g === 'mes' ? 'm:' : (g === 'semana' ? 'w:' : 'd:'));

    if (mode === 'personalizado') {
      // Janela [from, to] (inclusiva por dia) + granularidade escolhida. Uma coluna por
      // sub-período com cards; quem cai fora da janela vai p/ "Fora do período".
      const grain = (view && view.group_date_grain) || 'dia';
      const fromE = dayStartEpoch(view && view.group_date_from);
      const toE = dayEndEpoch(view && view.group_date_to);
      const inWin = (t) => (fromE == null || t >= fromE) && (toE == null || t <= toE);
      const keyOf = keyerFor(grain);
      const labelOf = labelerFor(grain);
      const prefix = prefixFor(grain);
      const inRows = (rows || []).filter((r) => r.opened_at && inWin(r.opened_at));
      const distinct = Array.from(new Set(inRows.map(keyOf).filter(Boolean))).sort().reverse();
      const cols = distinct.map((k) => ({ id: prefix + k, label: labelOf(k) }));
      if ((rows || []).some((r) => !inWin(r.opened_at || 0))) cols.push({ id: '__outofrange__', label: 'Fora do período' });
      return {
        readOnly: true,
        columns: cols.length ? cols : [{ id: '__outofrange__', label: 'Fora do período' }],
        columnIdOf: (r) => {
          const t = r.opened_at || 0;
          if (!inWin(t)) return '__outofrange__';
          const k = keyOf(r); return k ? prefix + k : '__outofrange__';
        },
      };
    }

    // dia | semana | mes — bucketiza todos os cards carregados por período.
    const keyOf = keyerFor(mode);
    const labelOf = labelerFor(mode);
    const prefix = prefixFor(mode);
    const distinct = Array.from(new Set((rows || []).map(keyOf).filter(Boolean))).sort().reverse();
    const cols = distinct.map((k) => ({ id: prefix + k, label: labelOf(k) }));
    if ((rows || []).some((r) => !r.opened_at)) cols.push({ id: '__nodate__', label: 'Sem data' });
    return {
      readOnly: true,
      columns: cols.length ? cols : [{ id: '__nodate__', label: 'Sem data' }],
      columnIdOf: (r) => { const k = keyOf(r); return k ? prefix + k : '__nodate__'; },
    };
  }

  if (gb === 'pfield') {
    const scope = view && view.group_field_scope;
    const key = view && view.group_attr_key;
    const def = (groupFields || []).find((d) => d.scope === scope && d.key === key);
    if (!def) {
      return {
        readOnly: true, unavailable: true,
        columns: [{ id: '__none__', label: 'Campo indisponível' }],
        columnIdOf: () => '__none__',
      };
    }
    const opts = Array.isArray(def.options) ? def.options : [];
    // Valor do campo na row: escopo protocolo → `fields`; atendimento → `atendimento_fields`.
    // checkboxes de valor único guarda lista de 1 item → usa o 1º.
    const valOf = (r) => {
      const src = (scope === 'protocolo' ? r.fields : r.atendimento_fields) || {};
      let v = src[key];
      if (Array.isArray(v)) v = v.length ? v[0] : '';
      return v;
    };
    return {
      columns: [{ id: '__none__', label: 'Sem valor' },
                ...opts.map((o) => ({ id: `o:${o}`, label: o }))],
      columnIdOf: (r) => { const v = valOf(r); return (v != null && v !== '') ? `o:${v}` : '__none__'; },
      confirmText: (r, col) => (col === '__none__'
        ? `Limpar "${def.label}" do protocolo de ${cliente(r)}?`
        : `Definir "${def.label}" = "${col.slice(2)}" no protocolo de ${cliente(r)}?`),
      onDrop: (r, col) => apiPost(`/protocolos/${r.id}/set-field`, {
        scope, key, value: col === '__none__' ? null : col.slice(2),
      }),
    };
  }

  if (gb === 'attr') {  // legado: agrupamento por atributo de conversa (descontinuado)
    return {
      readOnly: true, unavailable: true,
      columns: [{ id: '__none__', label: 'Agrupamento indisponível' }],
      columnIdOf: () => '__none__',
    };
  }

  // status (built-in, default)
  return {
    columns: [{ id: 'aberto', label: 'Aberto' }, { id: 'fechado', label: 'Fechado' }],
    columnIdOf: (r) => (r.status === 'fechado' ? 'fechado' : 'aberto'),
    confirmText: (r, col) => (col === 'fechado'
      ? `Finalizar o protocolo de ${cliente(r)}?`
      : `Reabrir o protocolo de ${cliente(r)}?`),
    onDrop: (r, col) => (col === 'fechado'
      ? apiPost(`/protocolos/${r.id}/close`) : apiPost(`/protocolos/${r.id}/reopen`)),
  };
}

// Reordena as colunas geradas por `buildGrouping` segundo uma lista salva de ids. As colunas
// cujo id está em `order` vêm primeiro (na ordem de `order`); as demais (dinâmicas novas,
// ausentes da ordem salva) seguem na ordem natural, no fim. order vazio/nulo → intacto.
function applyColumnOrder(columns, order) {
  if (!Array.isArray(order) || !order.length) return columns;
  const byId = new Map(columns.map((c) => [c.id, c]));
  const seen = new Set();
  const out = [];
  for (const id of order) { const c = byId.get(id); if (c && !seen.has(id)) { out.push(c); seen.add(id); } }
  for (const c of columns) if (!seen.has(c.id)) out.push(c);
  return out;
}

export function ProtocolosTab({ api, setTab }) {
  // Alternância Kanban/Lista vive AQUI (topo, ao lado do título) — o modo é passado ao
  // ProtocolosList por prop. Persistido por-device em localStorage (MODE_KEY).
  const [mode, setMode] = useState(() => lsGet(MODE_KEY, 'lista'));  // 'lista' | 'kanban'
  const setM = (m) => { setMode(m); lsSet(MODE_KEY, m); };
  // O header (Voltar + título + Kanban/Lista) foi fundido na toolbar renderizada DENTRO de
  // ProtocolosList — junto da contagem, Buscar e ações — para aproveitar o topo. Aqui só ficam
  // o estado do modo (persistido por-device) e o repasse por props.
  return html`<${ProtocolosList} api=${api} mode=${mode} setMode=${setM} setTab=${setTab} />`;
}

// UMA coluna do Kanban: pagina SOZINHA (scroll infinito próprio dentro da coluna) via
// /grouped/column. O total do cabeçalho vem do índice do SERVIDOR — não do que já foi
// carregado —, então a contagem é exata mesmo com a coluna só parcialmente baixada.
function KanbanColumn({ col, fetchColumnPage, resetKey, canDrag, dragRef, draggedRef,
                        dropCol, setDropCol, colDragRef, colDropTarget, setColDropTarget,
                        reorderColumns, applyDrop, openDetail }) {
  const fetchPage = useCallback((offset) => fetchColumnPage(col.id, offset),
                                [fetchColumnPage, col.id]);
  const { items: cards, loading, loadingMore, hasMore, loadMore } = useInfiniteScroll({
    fetchPage, pageSize: PAGE_SIZE, resetKey: `${resetKey}|${col.id}`, keyOf: (r) => r.id });
  const bodyRef = useRef(null);

  // Carregar mais AO ROLAR a própria coluna. Usamos o evento de scroll do container em
  // vez de um IntersectionObserver com `root` de elemento: o sentinela dentro da coluna
  // não disparava de forma confiável (a lista fica presa nos primeiros 50). O guard de
  // concorrência é o próprio hook (busyRef) + `hasMore`.
  const onScroll = useCallback((e) => {
    const el = e.currentTarget;
    if (!el || !hasMore || loadingMore) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 300) loadMore();
  }, [hasMore, loadingMore, loadMore]);

  // Se o que já carregou NÃO enche a coluna, não há como rolar — completa sozinho até
  // dar scroll (ou acabar). Cobre colunas curtas e telas altas.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el || !hasMore || loadingMore || loading) return;
    if (el.scrollHeight <= el.clientHeight + 4) loadMore();
  }, [cards.length, hasMore, loadingMore, loading, loadMore]);

  const isTarget = dropCol === col.id;
  const isColTarget = colDropTarget === col.id;
  return html`
    <div class="flex flex-col w-72 shrink-0">
      <div draggable=${true}
        onDragStart=${(e) => { colDragRef.current = col.id; try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(col.id)); } catch (_) { /* ignore */ } }}
        onDragEnd=${() => { colDragRef.current = null; setColDropTarget(null); }}
        onDragOver=${(e) => { if (!colDragRef.current) return; e.preventDefault(); if (colDropTarget !== col.id) setColDropTarget(col.id); }}
        onDragLeave=${() => setColDropTarget((d) => (d === col.id ? null : d))}
        onDrop=${(e) => { if (!colDragRef.current) return; e.preventDefault(); reorderColumns(colDragRef.current, col.id); colDragRef.current = null; setColDropTarget(null); }}
        title="Arraste para reordenar as colunas"
        class="flex items-center justify-between mb-2 px-1 py-0.5 rounded cursor-move select-none ${isColTarget ? 'ring-2 ring-wa-teal bg-wa-hover' : ''}">
        <span class="text-[13px] font-semibold text-wa-text truncate">${col.label}</span>
        <span class="text-[12px] text-wa-secondary">${col.total}</span>
      </div>
      <div ref=${bodyRef} onScroll=${onScroll}
        onDragOver=${(e) => { if (colDragRef.current) return; e.preventDefault(); if (dropCol !== col.id) setDropCol(col.id); }}
        onDragLeave=${() => setDropCol((d) => (d === col.id ? null : d))}
        onDrop=${(e) => { if (colDragRef.current) return; e.preventDefault(); const row = dragRef.current; setDropCol(null); applyDrop(row, col.id); }}
        class="flex-1 min-h-[120px] max-h-[70vh] overflow-y-auto rounded-lg p-2 flex flex-col gap-2 border border-dashed transition-colors ${isTarget ? 'ring-2 ring-wa-teal bg-wa-hover border-wa-teal' : 'bg-wa-bg border-wa-border'}">
        ${loading ? html`<div class="text-[12px] text-wa-secondary text-center py-4">Carregando…</div>`
          : cards.length === 0 ? html`<div class="text-[12px] text-wa-secondary text-center py-4">Vazio</div>`
          : cards.map((r) => html`<${KanbanCard} key=${r.id} row=${r}
              draggedRef=${draggedRef} dragRef=${dragRef} canDrag=${canDrag}
              onClearDrop=${() => setDropCol(null)} onOpen=${() => openDetail(r.id)} />`)}
        ${loadingMore ? html`<div class="text-[12px] text-wa-secondary text-center py-1 shrink-0">Carregando mais…</div>` : null}
        ${!loadingMore && hasMore ? html`<div class="text-[11px] text-wa-secondary text-center py-1 shrink-0">Role para carregar mais (${cards.length}/${col.total})</div>` : null}
      </div>
    </div>`;
}

function ProtocolosList({ api, mode, setMode, setTab }) {
  const apiBase = api.apiBase;
  const { authHeaders } = api.services;

  const [cols, setCols] = useState([]);          // TODAS as defs do protocolo (OBS fixo + extras, com `required`)
  const [atendDefs, setAtendDefs] = useState([]);  // defs de "Resolver atendimento" (sub-tabela do detalhe)
  const [atendResolveDefs, setAtendResolveDefs] = useState([]); // atendimento: obs+extras editáveis (popup)
  const [contactAttrDefs, setContactAttrDefs] = useState([]);  // atributo de CONTATO do core (filtros; list=dropdown, resto=texto; não-sistema)
  const [channels, setChannels] = useState([]);  // canais disponíveis p/ o dropdown do filtro "Canal" ([{value,label}])
  const [status, setStatus] = useState([]);   // filtro Status — LISTA (multi): ['aberto'|'fechado']
  const [q, setQ] = useState('');
  // Padrão ao entrar/F5: intervalo começa HOJE e fica aberto p/ frente (sem teto).
  // Não é persistido — todo (re)carregamento da página volta a "hoje para frente".
  const [dateFrom, setDateFrom] = useState(() => ymd(new Date()));
  const [dateTo, setDateTo] = useState('');
  const [datePreset, setDatePreset] = useState(null);
  // Visualizações (abas de "Agrupar por"): built-ins + carregadas do backend.
  const [views, setViews] = useState([]);
  const [activeViewId, setActiveViewId] = useState(() => {
    const u = readUrlParam('view');                   // precedência URL > localStorage
    if (u) return u;
    const v = lsGet(VIEW_KEY, '');
    if (v) return v;
    const kg = lsGet(KGROUP_KEY, '');                 // migração suave da chave antiga
    return kg === 'atendente' ? '__atendente' : '__status';
  });
  const [currentUser, setCurrentUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem('whatsbot_user') || 'null'); } catch (e) { return null; }
  });
  const [assigneeFilter, setAssigneeFilter] = useState([]);  // filtro por atendente — LISTA (multi) de ids (string)
  const [notaFilter, setNotaFilter] = useState([]);   // filtro por nota de avaliação (1..5)
  const [attrFilters, setAttrFilters] = useState({});          // filtros por atributo de atendimento (da aba)
  const [sortBy, setSortBy] = useState(null);
  const [sortDir, setSortDir] = useState('asc');
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]);            // grupos de permissão (p/ "Quem pode ver")
  const [actionMsg, setActionMsg] = useState(null);  // {text, error}
  const [savingPref, setSavingPref] = useState(false);  // salvando filtros pessoais/equipe
  const [prefMsg, setPrefMsg] = useState('');           // feedback do salvar/alternar origem
  const [filtersOpen, setFiltersOpen] = useState(false); // botão "Filtros": expande os NÃO-favoritos
  const [settingsOpen, setSettingsOpen] = useState(false); // popover ⚙ do topo: origem + config de filtros da aba
  const settingsRef = useRef(null);                        // wrapper do popover ⚙ (fecha ao clicar fora)
  const [detail, setDetail] = useState(null);        // {protocolo, atendimentos}
  const [detailWarning, setDetailWarning] = useState(''); // aviso no detalhe (vindo do drag p/ "Fechado")
  const appliedViewRef = useRef(null);               // última aba cujos filtros já foram aplicados
  // Troca a aba ativa: estado + localStorage (por-device) + reflete ?view=<id> na URL
  // (replaceState, preservando ?detail=). id vazio limpa o param (ex.: view excluída).
  const setActiveView = (id) => {
    setActiveViewId(String(id)); lsSet(VIEW_KEY, String(id)); writeUrlParam('view', id ? String(id) : '');
  };

  const tabs = useMemo(() => views.map((v) => ({ ...v, label: v.name })), [views]);
  const activeView = useMemo(
    () => tabs.find((t) => String(t.id) === String(activeViewId)) || tabs[0] || FALLBACK_VIEW,
    [tabs, activeViewId]);
  // Permissões do plugin (P48 "esconder, não desabilitar"; default-allow p/ legado/
  // sem RBAC). `view` gate a tela inteira (conteúdo + os GETs); `edit` gate as AÇÕES
  // (finalizar, salvar campos, arrastar no Kanban → fechar/reabrir/atribuir).
  const can = (k) => (api.services.hasPermission
    ? api.services.hasPermission(currentUser, `plugin.protocolos.${k}`) : true);
  const canView = can('view');
  const canEdit = can('edit');
  const canTeam = api.services.hasPermission
    ? api.services.hasPermission(currentUser, 'plugin.protocolos.manage_team_views') : true;
  const canEditView = (v) => canTeam
    || (v.scope === 'personal' && (!currentUser || v.owner_user_id === currentUser.id));

  // Campos de "Configurações — Protocolos" (escopos protocolo + atendimento) que alimentam o
  // Kanban: `groupFields` = opção de valor único (agrupar/arrastar) — SÓ tipo opção; `filterFields`
  // = QUALQUER tipo de campo (filtrar: opção→dropdown, texto/número/data→busca parcial).
  // `cols` = defs do protocolo; `atendDefs` = de atendimento.
  const groupFields = useMemo(() => [
    ...optionFields(cols, 'protocolo', true),
    ...optionFields(atendDefs, 'atendimento', true),
  ], [cols, atendDefs]);
  const filterFields = useMemo(() => [
    ...filterableFields(cols, 'protocolo'),
    ...filterableFields(atendDefs, 'atendimento'),
  // "atendente" (rótulo FIXO, tipo `atendente`) NÃO é filtrável como campo de protocolo: seu
  // valor não vive em `fields` — roteia p/ o assignee NATIVO (assignee_user_id). Deixá-lo aqui
  // criava um filtro-fantasma (busca em valor inexistente → SEMPRE zero resultados) que ainda
  // duplicava o dropdown "Atendente" da seção Nativas — este sim é o filtro correto de atendente.
  ].filter((d) => d.type !== 'atendente'), [cols, atendDefs]);
  // Pode CRIAR novas visualizações (agrupamentos)? Gate do "+ Nova". create_views OU
  // manage_team_views (quem gerencia views de equipe também cria). Default-allow sem RBAC.
  const canCreateViews = (api.services.hasPermission
    ? api.services.hasPermission(currentUser, 'plugin.protocolos.create_views') : true) || canTeam;
  // Filtros disponíveis na aba ativa (available_filters da view; null/undefined = todos).
  // Gate da barra de filtros ao vivo. Chaves: status|atendente|q|periodo|canal|pf:<scope>:<key>|cattr:<key>.
  const availArr = (activeView && Array.isArray(activeView.available_filters)) ? activeView.available_filters : null;
  const availFilter = (key) => !availArr || availArr.includes(key);
  // Favoritos: sempre visíveis na barra; os demais disponíveis ficam atrás do botão "Filtros".
  const favArr = (activeView && Array.isArray(activeView.favorite_filters)) ? activeView.favorite_filters : [];
  const isFavorite = (key) => favArr.includes(key);

  const dragRef = useRef(null);     // protocolo sendo arrastado
  const draggedRef = useRef(false); // distingue clique de arrasto
  const [dropCol, setDropCol] = useState(null);
  // Reorder de COLUNAS (arrastar o título): ref separado do card p/ não colidir. A ordem só
  // é aplicada localmente (colOrderDraft) — persiste no "Salvar visualização pessoal/equipe".
  const colDragRef = useRef(null);              // id da coluna sendo arrastada
  const [colOrderDraft, setColOrderDraft] = useState(null);  // null = usar a ordem salva
  const [colDropTarget, setColDropTarget] = useState(null);  // realce da coluna alvo

  // Transporte via api.http do core: checa status HTTP, unifica o corpo de erro
  // ({error}/{detail}) e dispara o toast "Permissão negada." em 403 — some o falso
  // "sucesso" quando o back-end nega. getJson recebe URLs já absolutas (/api/...);
  // apiPost recebe paths relativos ao apiBase (/protocolos/…).
  const getJson = useCallback((url) => api.http.get(url), [api]);
  const apiPost = useCallback((path, body) => api.http.post(path, body), [api]);

  // Promise do carregamento de metadados em voo. O `openDetail` a aguarda antes de
  // montar o modal — sem ela o detalhe podia abrir com `cols = []` e o form nascia
  // vazio (inclusive o obrigatório "Atendente"). Nenhuma requisição extra: é a MESMA
  // promise já disparada no mount. Ver plano 88 · B0.
  const metaPromiseRef = useRef(null);

  // Metadados da aba (field-defs, atributos de contato, canais) — NÃO dependem dos
  // filtros; carregam uma vez (e a cada troca de apiBase), separados da lista paginada.
  const loadMeta = useCallback(async () => {
    const [dd, cd, co, ch] = await Promise.all([
      getJson(`${apiBase}/field-defs?scope=protocolo`),
      getJson(`${apiBase}/field-defs?scope=atendimento`),
      getJson('/api/custom-attributes?applies_to=contact'),       // atributos de CONTATO (filtros)
      getJson(`${apiBase}/canais`),                               // canais disponíveis (filtro Canal)
    ]);
    // TODAS as defs do protocolo (OBS fixo + extras, com `required`) — alimentam o
    // form editável do detalhe e o gate de obrigatórios (drag p/ "Fechado"). Sem filtrar.
    setCols((dd && dd.ok && dd.data && dd.data.defs) || []);
    const atendAll = (cd && cd.ok && cd.data && cd.data.defs) || [];
    setAtendDefs(atendAll.filter((d) => !d.fixed));
    setAtendResolveDefs(atendAll.filter((d) => !d.readonly)); // p/ o popup "Resolver atendimento"
    // Atributo de CONTATO (is_system=0) — alimenta os FILTROS do Kanban. Tipo list vira
    // dropdown (opções); os demais (texto/número/data) viram campo de digitação (busca parcial).
    setContactAttrDefs(((co && co.ok && co.data) || [])
      .filter((d) => !d.is_system)
      .map((d) => ({ key: d.attribute_key, label: d.display_name || d.attribute_key, type: d.type,
                     options: Array.isArray(d.options) ? d.options : [] })));
    // Canais disponíveis (não-arquivados) → opções do dropdown do filtro "Canal".
    setChannels(((ch && ch.ok && ch.data) || []).map((c) => ({ value: c.id, label: c.name || c.id })));
  }, [apiBase, getJson]);
  // Sem permissão de VER → não dispara os GETs (evita uma rajada de 403 no load).
  // Guarda a promise p/ o openDetail poder esperá-la (`null` quando não dispara → o
  // `await null` do openDetail resolve na hora, sem ramo especial).
  useEffect(() => { if (canView) metaPromiseRef.current = loadMeta(); }, [loadMeta, canView]);

  // Popover ⚙ fecha ao clicar fora (mesmo padrão do OptionListSelect).
  useEffect(() => {
    function onDoc(e) { if (settingsRef.current && !settingsRef.current.contains(e.target)) setSettingsOpen(false); }
    if (settingsOpen) document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [settingsOpen]);

  // Filtros → querystring da lista paginada de protocolos.
  const listParams = useCallback(() => {
    const params = new URLSearchParams();
    if (status.length) params.set('status', JSON.stringify(status));
    if (q.trim()) params.set('q', q.trim());
    const of = dayStartEpoch(dateFrom);
    const ot = dayEndEpoch(dateTo);
    if (of != null) params.set('opened_from', String(of));
    if (ot != null) params.set('opened_to', String(ot));
    if (assigneeFilter.length) params.set('assignee_user_id', JSON.stringify(assigneeFilter));
    // Nota de avaliação: fica AQUI (e não só na lista) porque `listParams` também alimenta
    // `groupParams` — as rotas /grouped/* precisam do mesmo recorte, senão as contagens
    // por coluna do Kanban ignorariam o filtro que a lista aplica.
    if (notaFilter.length) params.set('nota', JSON.stringify(notaFilter));
    if (attrFilters && Object.keys(attrFilters).length) params.set('attr_filters', JSON.stringify(attrFilters));
    return params;
  }, [status, q, dateFrom, dateTo, assigneeFilter, notaFilter, attrFilters]);

  // Bump manual → re-busca da 1ª página (botão Atualizar, WS, pós-ação no detalhe).
  const [reloadTick, setReloadTick] = useState(0);
  const reload = useCallback(() => setReloadTick((t) => t + 1), []);
  const [totalCount, setTotalCount] = useState(0);  // total do servidor (badge "N protocolos")

  // fetchPage(offset) → uma página do envelope {items,total,has_more} (plano 50). Só a
  // LISTA usa esta rota — no Kanban quem pagina é cada coluna (/grouped/column). Sem
  // permissão de VER devolve vazio (não dispara GET ⇒ sem rajada de 403).
  const fetchPage = useCallback(async (offset) => {
    if (!canView || mode === 'kanban') return { items: [], hasMore: false };
    const params = listParams();
    params.set('limit', String(PAGE_SIZE));
    params.set('offset', String(offset));
    const ll = await getJson(`${apiBase}/protocolos?${params.toString()}`);
    const env = (ll && ll.ok && ll.data) || {};
    // Envelope novo {items,...}; tolera shape legado (lista pura) por segurança.
    const items = Array.isArray(env) ? env : (Array.isArray(env.items) ? env.items : []);
    if (typeof env.total === 'number') setTotalCount(env.total);
    return { items, hasMore: !!env.has_more };
  }, [apiBase, canView, mode, listParams, getJson]);

  // resetKey: qualquer mudança de filtro / permissão / modo / reload volta à 1ª página.
  const resetKey = useMemo(
    () => JSON.stringify([status, q, dateFrom, dateTo, assigneeFilter, notaFilter, attrFilters, canView, mode, reloadTick]),
    [status, q, dateFrom, dateTo, assigneeFilter, notaFilter, attrFilters, canView, mode, reloadTick]);

  const { items: rows, loading, loadingMore, hasMore, loadMore } =
    useInfiniteScroll({ fetchPage, pageSize: PAGE_SIZE, resetKey, keyOf: (r) => r.id });

  // Scroll infinito da LISTA: escuta o scroll de quem realmente rola (janela OU container
  // interno do painel). Mesma abordagem das colunas do Kanban — o sentinela por
  // IntersectionObserver não disparava aqui e a lista ficava presa nos primeiros 50.
  const listEndRef = useRef(null);
  useEffect(() => {
    if (mode !== 'lista' || !hasMore) return undefined;
    const marker = listEndRef.current;
    if (!marker) return undefined;
    const sc = scrollParentOf(marker);
    const target = sc || window;
    const onScroll = () => { if (hasMore && !loadingMore && nearBottom(sc)) loadMore(); };
    target.addEventListener('scroll', onScroll, { passive: true });
    onScroll();   // conteúdo que não enche a tela nunca geraria scroll: completa sozinho
    return () => target.removeEventListener('scroll', onScroll);
  }, [mode, hasMore, loadingMore, loadMore, rows.length]);

  // ── Kanban agrupado NO SERVIDOR ────────────────────────────────────────────
  // As colunas e a contagem exata de cada uma vêm do índice do backend
  // (/grouped/columns); cada coluna pagina sozinha (/grouped/column). O navegador
  // nunca baixa a coleção inteira para agrupar.
  const [kanban, setKanban] = useState({ columns: [], truncated: false, unavailable: false });
  const [kanbanLoading, setKanbanLoading] = useState(false);

  // Filtros + config de agrupamento da aba ativa → query params das rotas /grouped/*.
  const groupParams = useCallback(() => {
    const p = listParams();
    const v = activeView || {};
    p.set('group_by', v.group_by || 'status');
    for (const k of ['group_attr_key', 'group_field_scope', 'group_date_mode',
                     'group_date_grain', 'group_date_from', 'group_date_to']) {
      if (v[k]) p.set(k, String(v[k]));
    }
    return p;
  }, [listParams, activeView]);

  // Muda ⇒ recarrega colunas e reinicia a paginação de TODAS as colunas.
  const groupResetKey = useMemo(() => JSON.stringify([
    status, q, dateFrom, dateTo, assigneeFilter, notaFilter, attrFilters, canView, reloadTick,
    (activeView || {}).group_by, (activeView || {}).group_attr_key,
    (activeView || {}).group_field_scope, (activeView || {}).group_date_mode,
    (activeView || {}).group_date_grain, (activeView || {}).group_date_from,
    (activeView || {}).group_date_to,
  ]), [status, q, dateFrom, dateTo, assigneeFilter, notaFilter, attrFilters, canView, reloadTick, activeView]);

  useEffect(() => {
    if (!canView || mode !== 'kanban') return undefined;
    let alive = true;
    setKanbanLoading(true);
    (async () => {
      try {
        const r = await getJson(`${apiBase}/grouped/columns?${groupParams().toString()}`);
        const d = (r && r.ok && r.data) || {};
        if (alive) {
          setKanban({ columns: Array.isArray(d.columns) ? d.columns : [],
                      truncated: !!d.truncated, unavailable: !!d.unavailable });
        }
      } finally { if (alive) setKanbanLoading(false); }
    })();
    return () => { alive = false; };
  }, [mode, groupResetKey]);   // groupParams/getJson lidos no corpo (groupResetKey cobre as entradas)

  const fetchColumnPage = useCallback(async (colId, offset) => {
    if (!canView) return { items: [], hasMore: false };
    const p = groupParams();
    p.set('col_id', colId);
    p.set('limit', String(PAGE_SIZE));
    p.set('offset', String(offset));
    const r = await getJson(`${apiBase}/grouped/column?${p.toString()}`);
    const d = (r && r.ok && r.data) || {};
    return { items: Array.isArray(d.items) ? d.items : [], hasMore: !!d.has_more };
  }, [apiBase, canView, groupParams, getJson]);

  // Visualizações (abas) + usuário atual (p/ permissão de equipe).
  const loadViews = useCallback(async () => {
    try { const r = await getJson(`${apiBase}/kanban-views`); if (r && r.ok) setViews(r.data || []); }
    catch (e) { /* ignore */ }
  }, [apiBase, getJson]);
  useEffect(() => { if (canView) loadViews(); }, [loadViews, canView]);
  // Grupos de permissão (roles) p/ o seletor "Quem pode ver" do editor.
  useEffect(() => {
    if (!canView) return;
    let alive = true;
    getJson(`${apiBase}/roles`).then((r) => { if (alive && r && r.ok && r.data) setRoles(r.data.roles || []); }).catch(() => {});
    return () => { alive = false; };
  }, [apiBase, getJson, canView]);
  useEffect(() => {
    if (!api.services.getMe) return;
    api.services.getMe().then((r) => { if (r && r.ok && r.data && r.data.user) setCurrentUser(r.data.user); }).catch(() => {});
  }, [api]);

  // Aplica um dict de filtros pré-determinados na BARRA ao vivo. Só aplica os tipos
  // DISPONÍVEIS na aba (availFilter); um tipo indisponível nunca constrange silenciosamente.
  // Data: preset relativo vira intervalo REAL; 'tudo' limpa; from/to literais respeitados;
  // sem date → default (hoje→frente). Reusado pela troca de aba e pelo toggle Pessoal/Equipe.
  const applyFilterDict = (f) => {
    f = f || {};
    setStatus(availFilter('status') ? asFilterList(f.status) : []);
    setQ(availFilter('q') && f.q != null ? f.q : '');
    setAssigneeFilter(availFilter('atendente') ? asFilterList(f.assignee_user_id).map(String) : []);
    setNotaFilter(availFilter('nota') && f.nota != null ? asFilterList(f.nota).map(String) : []);
    setAttrFilters(f.attrs && typeof f.attrs === 'object'
      ? Object.fromEntries(Object.entries(f.attrs).filter(([k]) => availFilter(k))) : {});
    const df = (availFilter('periodo') && f.date && typeof f.date === 'object') ? f.date : null;
    const presetDays = df && df.preset && df.preset !== 'tudo'
      ? (DATE_PRESETS.find(([lbl]) => lbl === df.preset) || [])[1] : undefined;
    if (df && presetDays != null) {
      const t = new Date(); const fr = new Date(); fr.setDate(fr.getDate() - presetDays);
      setDatePreset(df.preset); setDateFrom(ymd(fr)); setDateTo(ymd(t));
    } else if (df && df.preset === 'tudo') {
      setDatePreset('tudo'); setDateFrom(''); setDateTo('');
    } else if (df && (df.from || df.to)) {
      setDatePreset(null); setDateFrom(df.from || ''); setDateTo(df.to || '');
    } else {
      setDatePreset(null); setDateFrom(ymd(new Date())); setDateTo('');  // default: hoje→frente
    }
  };

  // Aplica os filtros pré-determinados da aba UMA vez por troca de aba (defaults de
  // entrada, não travas). appliedViewRef evita reaplicar em reloads (WS) e só dispara
  // quando a view alvo já está resolvida (built-in sempre; custom após carregar).
  useEffect(() => {
    const v = activeView;
    const resolved = v && String(v.id) === String(activeViewId);
    if (!resolved || appliedViewRef.current === String(activeViewId)) return;
    appliedViewRef.current = String(activeViewId);
    // Origem dos filtros: a preferência do usuário (pessoal x equipe) por aba. Default =
    // filtros da EQUIPE (coluna filters compartilhada). Fallback de render não tem pref/filters.
    const usePersonal = v.pref && v.pref.use_personal;
    applyFilterDict((usePersonal ? (v.pref.personal_filters || {}) : (v.filters || {})) || {});
  }, [activeViewId, views, activeView]);

  // Estado do botão "Filtros" ao trocar de aba: recolhido quando a aba tem favoritos (eles já
  // aparecem sempre); expandido quando não há favoritos, p/ views existentes não ficarem com a
  // barra vazia (retrocompat).
  useEffect(() => {
    const fav = (activeView && Array.isArray(activeView.favorite_filters)) ? activeView.favorite_filters : [];
    setFiltersOpen(fav.length === 0);
  }, [activeViewId, activeView]);

  // Atendentes (p/ as colunas do kanban "por atendente"). Reusa o serviço do core.
  useEffect(() => {
    let alive = true;
    const p = (api.services.getAssignableAgents
      ? api.services.getAssignableAgents() : Promise.resolve(null));
    p.then((r) => { if (alive && r && r.ok && r.data) setUsers(r.data.users || []); }).catch(() => {});
    return () => { alive = false; };
  }, [api]);

  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let ws;
    try {
      ws = new WebSocket(`${proto}//${location.host}/ws`);
      ws.onmessage = (m) => { try { if (JSON.parse(m.data).event === 'plugin_protocolos_changed') { reload(); loadViews(); } } catch (_) { /* ignore */ } };
    } catch (_) { /* ignore */ }
    return () => { try { ws && ws.close(); } catch (_) { /* ignore */ } };
  }, [reload, loadViews]);

  // ── Filtro de período ───────────────────────────────────────────────────────
  const onManualDate = (f, t) => { setDateFrom(f); setDateTo(t); setDatePreset(null); };
  const onPresetDate = (days, label) => {
    const t = new Date(); const f = new Date(); f.setDate(f.getDate() - days);
    setDateFrom(ymd(f)); setDateTo(ymd(t)); setDatePreset(label);
  };
  const onClearDate = () => { setDateFrom(''); setDateTo(''); setDatePreset('tudo'); };

  // Algum filtro (visível ou pré-determinado da aba) ativo? Usado p/ mostrar "Limpar
  // filtros" — assim uma aba com filtro pré-determinado que zera o resultado nunca é um
  // mistério: o usuário vê os controles e limpa para ver todos.
  const hasViewFilters = !!(status.length || (q && q.trim()) || notaFilter.length || assigneeFilter.length
    || Object.keys(attrFilters).length || dateTo || (datePreset && datePreset !== null));
  const clearFilters = () => {
    setStatus([]); setQ(''); setNotaFilter([]); setAssigneeFilter([]); setAttrFilters({});
    setDatePreset(null); setDateFrom(ymd(new Date())); setDateTo('');
  };

  // Monta um dict de filtros a partir da BARRA ao vivo (o que o usuário vê agora), só com os
  // tipos DISPONÍVEIS na aba. Data: preset relativo/'tudo' preservados; intervalo manual vira
  // from/to literais; o default "hoje→frente" (sem ajuste) é omitido (= sem filtro de data).
  const buildCurrentFilters = () => {
    const f = {};
    if (availFilter('status') && status.length) f.status = status;
    if (availFilter('q') && q.trim()) f.q = q.trim();
    if (availFilter('atendente') && assigneeFilter.length) f.assignee_user_id = assigneeFilter;
    if (availFilter('nota') && notaFilter.length) f.nota = notaFilter;
    if (availFilter('periodo')) {
      if (datePreset && datePreset !== 'tudo') f.date = { preset: datePreset, from: '', to: '' };
      else if (datePreset === 'tudo') f.date = { preset: 'tudo', from: '', to: '' };
      else if ((dateFrom && dateFrom !== ymd(new Date())) || dateTo) f.date = { preset: '', from: dateFrom || '', to: dateTo || '' };
    }
    const attrs = {};
    for (const [k, v] of Object.entries(attrFilters || {})) if (availFilter(k) && v != null && v !== '' && (!Array.isArray(v) || v.length)) attrs[k] = v;
    if (Object.keys(attrs).length) f.attrs = attrs;
    return f;
  };

  // Toggle Pessoal/Equipe (parte de cima dos filtros): aplica na barra o conjunto escolhido e
  // persiste a preferência do usuário (my-pref) sem mexer nos VALORES salvos. appliedViewRef é
  // marcado p/ o effect de entrada não re-aplicar por cima.
  const switchSource = async (toPersonal) => {
    const v = activeView;
    if (!v || v.id == null || v.id === FALLBACK_VIEW.id) return;
    setPrefMsg('');
    setColOrderDraft(null);  // a ordem exibida passa a refletir a origem escolhida
    appliedViewRef.current = String(activeViewId);
    applyFilterDict(toPersonal ? ((v.pref && v.pref.personal_filters) || {}) : (v.filters || {}));
    setViews((vs) => vs.map((x) => String(x.id) === String(v.id)
      ? { ...x, pref: { ...(x.pref || {}), use_personal: toPersonal } } : x));
    try {
      await fetch(`${apiBase}/kanban-views/${v.id}/my-pref`, {
        method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_personal: toPersonal }),
      });
    } catch (_) { /* silencioso: a barra já reflete a escolha */ }
  };

  // Salva os filtros ATUAIS da barra como padrão PESSOAL desta aba (my-pref) e marca a origem
  // como pessoal. Não re-aplica (a barra já mostra o que foi salvo).
  const savePersonalFilters = async () => {
    const v = activeView;
    if (!v || v.id == null || v.id === FALLBACK_VIEW.id) return;
    setSavingPref(true); setPrefMsg('');
    const f = buildCurrentFilters();
    const co = currentOrderIds();  // ordem das colunas atual (visível) → parte da visualização
    try {
      const r = await fetch(`${apiBase}/kanban-views/${v.id}/my-pref`, {
        method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_personal: true, personal_filters: f, personal_column_order: co }),
      });
      const j = await r.json().catch(() => ({}));
      setSavingPref(false);
      if (!(j && j.ok)) { setPrefMsg('Falha ao salvar visualização pessoal.'); return; }
      setPrefMsg('Visualização pessoal salva.');
      appliedViewRef.current = String(activeViewId);
      setColOrderDraft(null);
      setViews((vs) => vs.map((x) => String(x.id) === String(v.id)
        ? { ...x, pref: { use_personal: true, personal_filters: f, personal_column_order: co } } : x));
    } catch (_) { setSavingPref(false); setPrefMsg('Falha ao salvar visualização pessoal.'); }
  };

  // Salva os filtros ATUAIS da barra como padrão da EQUIPE (kanban-views.filters). Só para
  // quem pode editar a visualização; available_filters é omitido → o backend preserva.
  const saveTeamFilters = async () => {
    const v = activeView;
    if (!v || v.id == null || v.id === FALLBACK_VIEW.id) return;
    setSavingPref(true); setPrefMsg('');
    const f = buildCurrentFilters();
    const co = currentOrderIds();  // ordem das colunas atual (visível) → parte da visualização
    try {
      const r = await fetch(`${apiBase}/kanban-views/${v.id}`, {
        method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: f, column_order: co }),
      });
      const j = await r.json().catch(() => ({}));
      setSavingPref(false);
      if (!(j && j.ok)) { setPrefMsg((j && j.error) || 'Falha ao salvar visualização da equipe.'); return; }
      setPrefMsg('Visualização da equipe salva.');
      appliedViewRef.current = String(activeViewId);  // barra já reflete; não re-aplicar
      setColOrderDraft(null);
      setViews((vs) => vs.map((x) => String(x.id) === String(v.id) ? { ...x, filters: f, column_order: co } : x));
    } catch (_) { setSavingPref(false); setPrefMsg('Falha ao salvar visualização da equipe.'); }
  };

  // Botão ÚNICO "Salvar visualização de filtros": salva na ORIGEM atual (Pessoal x Equipe),
  // sempre depois de um pop-up de confirmação. Reusa savePersonalFilters/saveTeamFilters como
  // a lógica de gravação (não duplica). Equipe sem permissão de editar é barrado antes (botão
  // desabilitado no render), então aqui só cai no caminho permitido.
  const saveViewFilters = async () => {
    const v = activeView;
    if (!v || v.id == null || v.id === FALLBACK_VIEW.id) return;
    const usePersonal = !!(v.pref && v.pref.use_personal);
    const ok = await api.ui.openModal((close) => html`<${ConfirmDialog}
      message=${`Salvar os filtros atuais como padrão da visualização ${usePersonal ? 'Pessoal' : 'da Equipe'}?`}
      okLabel="Salvar" onOk=${() => close(true)} onCancel=${() => close(false)} />`);
    if (!ok) return;
    if (usePersonal) await savePersonalFilters(); else await saveTeamFilters();
  };

  // Id do protocolo atualmente aberto no detalhe (espelha ?detail= na URL). Ref p/ o
  // sync bidirecional não depender do estado assíncrono e evitar reabrir/refetch em loop.
  const detailIdRef = useRef(null);

  const openDetail = useCallback(async (atid, warn = false) => {
    // Espera as definições de campo antes de montar o modal — o form é semeado no mount
    // e, sem elas, nasceria vazio (deep-link `?detail=`, F5, "Resolver e ir ao
    // protocolo"). É a mesma forma do popup de resolver, que nunca falha (extends.js).
    // try/catch OBRIGATÓRIO: loadMeta não trata erro, e uma rejeição abortaria a
    // abertura do detalhe — pior que o bug. Falhou? Abre assim mesmo; o efeito de seed
    // do DetailModal recupera quando (e se) as defs chegarem.
    try { await metaPromiseRef.current; } catch (_) { /* segue sem os metadados */ }
    const r = await getJson(`${apiBase}/protocolos/${atid}`);
    if (r && r.ok) {
      setDetailWarning(warn ? 'Preencha os campos obrigatórios para finalizar este protocolo.' : '');
      setDetail(r.data);
      // Deep-link (Plano 24): o protocolo aberto vira ?detail=<id> na URL (compartilhável).
      detailIdRef.current = String(atid);
      writeUrlParam('detail', String(atid));
    }
  }, [apiBase, getJson]);

  // Fecha o detalhe e limpa o ?detail= (preservando o ?view=).
  const closeDetail = useCallback(() => {
    setDetail(null); setDetailWarning('');
    detailIdRef.current = null;
    writeUrlParam('detail', '');
  }, []);

  // Deep-link do protocolo aberto (Plano 24): /protocolos?detail=<id> abre e MANTÉM o
  // param na URL (compartilhável — antes era one-shot). Lido no mount E em popstate (é
  // assim que o "Resolver e ir ao protocolo" chega, via pushState no extends.js). Sync
  // bidirecional: param novo → abre; param ausente com detalhe aberto (voltar) → fecha.
  // No mesmo passo ressincroniza a aba ativa com ?view= (o param vence o localStorage).
  useEffect(() => {
    const readDeep = () => {
      const v = readUrlParam('view');            // ?view= tem precedência (setState idempotente no Preact)
      if (v) { setActiveViewId(String(v)); lsSet(VIEW_KEY, String(v)); }
      const id = readUrlParam('detail');
      if (id) {
        const n = parseInt(id, 10);
        if (detailIdRef.current !== String(id) && !Number.isNaN(n)) openDetail(n);
      } else if (detailIdRef.current != null) {
        // URL sem ?detail= (ex.: voltar do navegador) → fecha o detalhe aberto.
        setDetail(null); setDetailWarning(''); detailIdRef.current = null;
      }
    };
    readDeep();
    window.addEventListener('popstate', readDeep);
    return () => window.removeEventListener('popstate', readDeep);
  }, [openDetail]);

  // ── Kanban: comportamento do DRAG (as COLUNAS vêm do servidor) ───────────────
  // `buildGrouping` continua sendo a fonte de `onDrop`/`confirmText`/`columnIdOf` (o
  // último só para detectar "já está nesta coluna" no drag). A LISTA de colunas e as
  // contagens agora vêm do índice do backend — por isso `rows: []` aqui.
  const grouping = useMemo(
    () => buildGrouping(activeView, { users, groupFields, rows: [], apiPost }),
    [activeView, users, groupFields, apiPost]);

  // Ordem das colunas salva desta aba: PESSOAL (pref) se a origem for pessoal, senão da EQUIPE
  // (view.column_order). Ambas podem ser null → ordem padrão de buildGrouping.
  const storedColumnOrder = (v) => {
    if (!v) return null;
    return (v.pref && v.pref.use_personal)
      ? (v.pref.personal_column_order || null) : (v.column_order || null);
  };
  // Colunas efetivamente exibidas: rascunho local (se o usuário arrastou) tem precedência
  // sobre a ordem salva. Reaplica sobre as colunas ATUAIS (dinâmicas continuam funcionando).
  // Badge "N protocolos": no Kanban é a soma das colunas (contagem do servidor); na
  // Lista, o `total` do envelope. O "+" indica resultado parcial (índice truncado).
  const shownTotal = useMemo(
    () => (mode === 'kanban'
      ? (kanban.columns || []).reduce((s, c) => s + (c.total || 0), 0)
      : totalCount),
    [mode, kanban, totalCount]);
  const shownPartial = mode === 'kanban' ? kanban.truncated : hasMore;

  // Colunas do SERVIDOR (com `total` exato), reordenadas pela preferência salva/rascunho —
  // a ordem continua sendo por id de coluna, então `column_order` antigo segue valendo.
  const orderedColumns = useMemo(
    () => applyColumnOrder(kanban.columns, colOrderDraft || storedColumnOrder(activeView)),
    [kanban, colOrderDraft, activeView]);
  // Troca de aba OU de origem (Pessoal/Equipe) → descarta o rascunho, refletindo o que está salvo.
  useEffect(() => { setColOrderDraft(null); },
    [activeViewId, !!(activeView && activeView.pref && activeView.pref.use_personal)]);
  // Move a coluna `fromId` para a posição da `toId` (só local; persiste no Salvar).
  const reorderColumns = (fromId, toId) => {
    if (fromId === toId) return;
    const ids = orderedColumns.map((c) => c.id);
    const from = ids.indexOf(fromId); const to = ids.indexOf(toId);
    if (from < 0 || to < 0) return;
    ids.splice(from, 1); ids.splice(to, 0, fromId);
    setColOrderDraft(ids);
  };
  const currentOrderIds = () => orderedColumns.map((c) => c.id);  // ordem visível p/ salvar

  // Abre o editor de visualização (criar quando view=null). NÃO troca de aba sozinho —
  // o usuário só sai da aba atual pelo "✕" dela ou clicando noutra aba (req. do usuário).
  // Uma aba nova aparece na barra; editar a aba ATIVA re-aplica seus filtros (reset do ref).
  const openViewEditor = useCallback(async (view) => {
    const saved = await api.ui.openModal((close) => html`<${ViewEditorModal}
      view=${view} groupFields=${groupFields} users=${users} roles=${roles} canTeam=${canTeam}
      currentUser=${currentUser} api=${api}
      onSaved=${(v) => close(v)} onCancel=${() => close(null)} />`);
    if (saved) {
      if (view && String(view.id) === String(activeViewId)) appliedViewRef.current = null;
      await loadViews();
    }
  }, [api, groupFields, users, roles, canTeam, currentUser, loadViews, activeViewId]);

  const removeView = useCallback(async (view) => {
    if (!view) return;
    const ok = await api.ui.openModal((close) => html`<${ConfirmDialog} danger=${true} okLabel="Excluir"
      message=${`Excluir a visualização "${view.label || view.name}"?`}
      onOk=${() => close(true)} onCancel=${() => close(false)} />`);
    if (!ok) return;
    try {
      const r = await fetch(`${apiBase}/kanban-views/${view.id}`, { method: 'DELETE', headers: authHeaders() });
      const j = await r.json().catch(() => ({}));
      if (j && j.ok) { if (String(activeViewId) === String(view.id)) setActiveView(''); await loadViews(); }
      else setActionMsg({ text: (j && j.error) || 'Falha ao excluir.', error: true });
    } catch (_) { setActionMsg({ text: 'Falha ao excluir a visualização.', error: true }); }
  }, [api, apiBase, authHeaders, activeViewId, loadViews]);

  // Resolve a atendimento do ciclo aberto do protocolo (popup) e finaliza. Usado quando
  // o backend recusa o fechamento por haver atendimento aberta. Retorna true se finalizou.
  async function forceResolveAndClose(atid) {
    const r = await getJson(`${apiBase}/protocolos/${atid}`);
    const atendimentos = (r && r.ok && r.data && r.data.atendimentos) || [];
    const openCycle = atendimentos.find((c) => !c.ended_at && c.conversation_id);
    if (!openCycle) return false; // nada aberto → mantém o erro original
    let fields = {};
    if (atendResolveDefs.length) {
      const picked = await api.ui.openModal((close) => html`
        <${ResolveForm} defs=${atendResolveDefs}
          defaultAssignee=${currentUser && currentUser.id}
          defaultAssigneeName=${currentUser && (currentUser.name || currentUser.email)}
          onOk=${(v) => close(v)} onCancel=${() => close(null)} />`);
      if (!picked) return false; // cancelou → não finaliza
      fields = picked.fields || {}; // onOk devolve { fields, custom_attributes, goTo }
    }
    const res = await apiPost(`/atendimentos/${openCycle.conversation_id}/resolve`, { fields });
    if (!res || res.ok === false) {
      setActionMsg({ text: (res && res.error) || 'Falha ao resolver o atendimento.', error: true });
      return false;
    }
    // Fecha a ATENDIMENTO no core (status=closed). O resolve do plugin só encerra o CICLO
    // (ended_at) — sem este passo a atendimento segue ABERTA na aba de atendimento (o header
    // fica em "Resolver" em vez de "Reabrir"). É o MESMO 2º passo que a aba de atendimento
    // faz após o beforeResolve (resolveConversation → setConversationStatus); aqui o popup
    // do plugin já rodou, então chamamos o status direto. Mantém as duas abas em sincronia.
    const st = await api.services.setConversationStatus(openCycle.conversation_id, 'closed');
    if (st && st.ok === false) {
      setActionMsg({ text: st.error || 'Falha ao fechar o atendimento.', error: true });
      return false;
    }
    const closed = await apiPost(`/protocolos/${atid}/close`);
    if (closed && closed.ok === false) {
      setActionMsg({ text: closed.error || 'Falha ao finalizar.', error: true });
      return false;
    }
    return true;
  }

  // Finaliza o protocolo a partir do detalhe (Req 3/4). Se o backend recusar por haver
  // atendimento aberta, abre o popup "Resolver atendimento" (forceResolveAndClose) e finaliza
  // depois. Sucesso → fecha o modal + recarrega. Outro erro → devolve {ok:false,error}
  // p/ o DetailModal exibir inline.
  async function finalizeProtocolo(atid) {
    const res = await apiPost(`/protocolos/${atid}/close`);
    if (res && res.ok === false) {
      if (/atendimento abert[ao]/i.test(res.error || '')) {
        const done = await forceResolveAndClose(atid);
        if (done) { closeDetail(); reload(); return { ok: true }; }
        return { ok: false, error: 'Resolva o atendimento aberto para finalizar o protocolo.' };
      }
      return { ok: false, error: res.error || 'Falha ao finalizar.' };
    }
    closeDetail(); reload();
    return { ok: true };
  }

  // Obrigatórios do PROTOCOLO faltando (mesma regra do backend _missing_required):
  // extras das defs `required` (obs incluso) + o rótulo Atendente (assignee nativo);
  // checkbox sempre conta como preenchido.
  function missingRequiredProto(row) {
    const eff = { ...((row && row.fields) || {}) };
    for (const d of cols) if (d.type === 'atendente') eff[d.key] = (row && row.assignee_user_id);
    return cols.some((d) => d.required && !isFilledProto(d, eff[d.key]));
  }

  async function applyDrop(row, colId) {
    if (!canEdit) return;                            // sem permissão de editar → não muta
    if (!row || !grouping.onDrop) return;            // modo só-leitura (data) não muta
    if (grouping.columnIdOf(row) === colId) return;  // já está na coluna
    setActionMsg(null);
    // Confirmação OBRIGATÓRIA: descreve EXATAMENTE o que será feito antes de QUALQUER
    // alteração. Só prossegue ao confirmar.
    const msg = grouping.confirmText ? grouping.confirmText(row, colId) : 'Confirmar esta alteração?';
    const ok = await api.ui.openModal((close) => html`<${ConfirmDialog} message=${msg}
      onOk=${() => close(true)} onCancel=${() => close(false)} />`);
    if (!ok) return;
    // Req 5: soltar em "Fechado" sem os obrigatórios do protocolo → abre o detalhe
    //        daquele protocolo com um aviso e NÃO fecha.
    if (colId === 'fechado' && missingRequiredProto(row)) {
      openDetail(row.id, true);
      return;
    }
    try {
      const res = await grouping.onDrop(row, colId);
      if (res && res.ok === false) {
        // Fechar barrado por atendimento aberta → força o popup "Resolver atendimento".
        if (colId === 'fechado' && /atendimento abert[ao]/i.test(res.error || '')) {
          await forceResolveAndClose(row.id);
        } else {
          setActionMsg({ text: res.error || 'Falha ao mover.', error: true });
        }
      }
    } catch (_) { setActionMsg({ text: 'Falha ao mover o protocolo.', error: true }); }
    reload();
  }

  // ── Lista: colunas data-driven (ordenáveis) ──────────────────────────────────
  // Só os dados PRÓPRIOS do protocolo. Os rótulos do plugin e os atributos
  // personalizados NÃO aparecem aqui — eles vivem no DETALHE de cada protocolo
  // (modal), junto da atendimento a que pertencem.
  const allCols = useMemo(() => [
    { key: 'cliente', label: 'Cliente', nowrap: true,
      get: (r) => (r.contact_name || r.contact_phone || '').toLowerCase(),
      render: (r) => r.contact_name || r.contact_phone || '—' },
    { key: 'opened_at', label: 'Data criação', nowrap: true,
      get: (r) => r.opened_at || 0, render: (r) => fmtTs(r.opened_at) },
    { key: 'closed_at', label: 'Data fechamento', nowrap: true,
      get: (r) => r.closed_at || 0, render: (r) => fmtTs(r.closed_at) },
    { key: 'atendente', label: 'Atendente', nowrap: true,
      get: (r) => (r.assignee_name || '').toLowerCase(), render: (r) => r.assignee_name || '—' },
    { key: 'status', label: 'Status', nowrap: true,
      get: (r) => r.status || '', render: (r) => (r.status === 'aberto' ? 'ABERTO' : 'FECHADO') },
  ], []);

  const sorted = useMemo(() => {
    if (!sortBy) return rows;
    const col = allCols.find((c) => c.key === sortBy);
    if (!col) return rows;
    const dir = sortDir === 'desc' ? -1 : 1;
    return [...rows].sort((a, b) => {
      const va = col.get(a); const vb = col.get(b);
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
      return String(va).localeCompare(String(vb), 'pt-BR') * dir;
    });
  }, [rows, sortBy, sortDir, allCols]);

  function toggleSort(key) {
    if (sortBy === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortBy(key); setSortDir('asc'); }
  }

  const listaView = html`
    <div class="overflow-x-auto rounded-lg border border-wa-border">
      <table class="w-full text-[13px]">
        <thead><tr class="bg-wa-panel text-wa-text text-left">
          ${allCols.map((c) => html`<th key=${c.key} onClick=${() => toggleSort(c.key)}
            class="px-3 py-2 font-semibold whitespace-nowrap cursor-pointer select-none hover:bg-wa-hover">
            ${c.label}${sortBy === c.key ? html`<span class="ml-1 text-wa-teal">${sortDir === 'asc' ? '▲' : '▼'}</span>` : null}
          </th>`)}
        </tr></thead>
        <tbody>
          ${sorted.map((r) => html`<tr key=${r.id} onClick=${() => openDetail(r.id)}
            class="border-t border-wa-border text-wa-text hover:bg-wa-hover cursor-pointer">
            ${allCols.map((c) => html`<td key=${c.key} class="px-3 py-2 ${c.nowrap ? 'whitespace-nowrap' : ''}">${c.render(r)}</td>`)}
          </tr>`)}
        </tbody>
      </table>
    </div>`;

  // Barra de abas "Agrupar por" — renderizada SEMPRE (kanban e lista, e mesmo quando vazio),
  // FORA do kanbanView, p/ a aba selecionada nunca sumir. Sai-se de uma aba só pelo "✕" dela.
  const tabBar = html`
    <div class="flex flex-wrap items-center gap-2 mb-3">
      <span class="text-[12px] text-wa-secondary">Agrupar por</span>
      <div class="flex flex-wrap items-center gap-1.5">
        ${tabs.map((t) => html`
          <div key=${t.id} class="inline-flex items-center rounded-lg border overflow-hidden ${String(activeViewId) === String(t.id) ? 'border-wa-teal' : 'border-wa-border'}">
            <button onClick=${() => setActiveView(t.id)}
              class="px-3 py-1 text-[12px] ${String(activeViewId) === String(t.id) ? 'bg-wa-teal text-white' : 'bg-wa-panel text-wa-text hover:bg-wa-hover'}"
              title=${t.scope === 'team' ? 'Visualização de equipe' : 'Visualização pessoal'}>
              ${t.label}${t.scope === 'team' ? html`<span class="ml-1 opacity-70">·equipe</span>` : null}
            </button>
            ${canEditView(t) ? html`
            <button title="Editar" onClick=${() => openViewEditor(t)}
              class="px-1.5 py-1 text-[12px] bg-wa-panel text-wa-secondary hover:bg-wa-hover hover:text-wa-text border-l border-wa-border">✎</button>` : null}
            ${canEditView(t) ? html`
              <button title="Excluir" onClick=${() => removeView(t)}
                class="px-1.5 py-1 text-[12px] bg-wa-panel text-wa-secondary hover:bg-wa-hover hover:text-red-500 border-l border-wa-border">✕</button>` : null}
          </div>`)}
        ${canCreateViews ? html`<button title="Novo agrupamento" onClick=${() => openViewEditor(null)}
          class="px-2.5 py-1 text-[12px] rounded-lg border border-dashed border-wa-border text-wa-text hover:bg-wa-hover">+ Nova</button>` : null}
      </div>
      ${mode === 'kanban' ? html`<span title=${[
          (grouping.onDrop && canEdit) ? 'Arraste um card para outra coluna (pede confirmação).' : null,
          'Arraste o título de uma coluna para reordenar (salve para manter).',
        ].filter(Boolean).join('\n')}
        class="inline-flex items-center justify-center w-4 h-4 rounded-full border border-wa-border text-[10px] text-wa-secondary cursor-help select-none">i</span>` : null}
      ${mode === 'kanban' && grouping.unavailable ? html`<span class="text-[11px] text-amber-600">Atributo da visualização indisponível (foi removido).</span>` : null}
    </div>`;

  const kanbanView = html`
    <div>
      ${actionMsg ? html`<div class="mb-2 px-3 py-2 rounded-md text-[13px] ${actionMsg.error ? 'bg-red-500/15 border border-red-500 text-red-500 font-semibold' : 'text-wa-secondary'}">${actionMsg.text}</div>` : null}
      ${kanban.truncated ? html`<div class="mb-2 text-[12px] text-amber-600">
        Muitos protocolos para agrupar de uma vez — as contagens por coluna são um piso.
        Refine os filtros para um resultado exato.</div>` : null}
      <div class="flex gap-3 overflow-x-auto pb-2">
        ${orderedColumns.map((col) => html`<${KanbanColumn} key=${col.id} col=${col}
          fetchColumnPage=${fetchColumnPage} resetKey=${groupResetKey}
          canDrag=${!!grouping.onDrop && canEdit}
          dragRef=${dragRef} draggedRef=${draggedRef}
          dropCol=${dropCol} setDropCol=${setDropCol}
          colDragRef=${colDragRef} colDropTarget=${colDropTarget} setColDropTarget=${setColDropTarget}
          reorderColumns=${reorderColumns} applyDrop=${applyDrop} openDetail=${openDetail} />`)}
      </div>
    </div>`;

  // Cluster esquerdo da toolbar (Voltar + título) — o header migrou de ProtocolosTab para cá,
  // então este fragmento é reusado tanto na toolbar principal quanto no branch !canView (para
  // o usuário sem permissão ainda conseguir voltar).
  const backAndTitle = html`
    ${setTab ? html`<button onClick=${() => setTab('contacts')}
      class="px-3 py-1.5 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover inline-flex items-center gap-1.5 whitespace-nowrap">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z"/></svg>
      Voltar
    </button>` : null}
    <h1 class="text-lg font-semibold text-wa-text">Protocolos</h1>`;

  // Sem permissão de VER → só Voltar + título + aviso (nenhum dado é carregado; ver os
  // useEffect gated por canView acima). P48: esconder o conteúdo, não desabilitar.
  if (!canView) {
    return html`
      <div>
        <div class="flex flex-wrap items-center gap-2 mb-3">${backAndTitle}</div>
        <div class="p-6 text-[13px] text-wa-secondary">
          Você não tem permissão para ver os protocolos.
        </div>
      </div>`;
  }

  // ── Barra de filtros categorizada ────────────────────────────────────────────
  // Mesmo padrão do painel "Configurar filtros da aba" → "Todas": as 5 categorias de
  // filterCategories() viram seções (título maiúsculo + linha separadora entre elas).
  // Puramente apresentação — estado, availFilter, attrFilters e serialização inalterados.
  // Renderiza UM campo dinâmico (é o corpo do antigo .map da barra plana): opção→dropdown,
  // demais tipos→input de busca parcial. `d = { fk, label, options }`.
  // Rótulo INLINE: o nome do campo vira o placeholder (sem <label> empilhada). `title` mantém o
  // nome completo acessível no hover mesmo quando o placeholder é truncado pela largura.
  const renderDynField = (d) => html`
    <div key=${d.fk} class="w-[180px]" title=${d.label}>
      ${(d.options && d.options.length)
        ? html`<${OptionListSelect} options=${d.options} multiple=${true} value=${asFilterList(attrFilters[d.fk])}
            onChange=${(v) => setAttrFilters((s) => { const n = { ...s }; if (v && v.length) n[d.fk] = v; else delete n[d.fk]; return n; })}
            placeholder=${d.label} float=${true} />`
        : html`<input class="wa-field w-full px-3 py-2 rounded-md text-[13px]" type="text"
            value=${attrFilters[d.fk] || ''} placeholder=${d.label}
            onInput=${(e) => setAttrFilters((s) => { const n = { ...s }; const v = e.target.value; if (v) n[d.fk] = v; else delete n[d.fk]; return n; })} />`}
    </div>`;

  // Campos nativos (widgets próprios), cada um gated pelo seu availFilter — compõem a seção
  // "Nativas". Cada item guarda a CHAVE p/ o particionamento favorito x não-favorito abaixo.
  // Rótulos INLINE: o nome do filtro vira o placeholder do OptionListSelect (aparece quando nada
  // está selecionado) — sem <label> empilhada, a barra fica mais baixa. Período mantém um rótulo
  // curto inline (date input não tem placeholder).
  // Buscar (`q`) é SEMPRE o primeiro item desta lista → como "Nativas" é a 1ª categoria e a ordem
  // de render vem da posição em nativeItems (não da ordem em que o filtro foi habilitado na config),
  // Buscar aparece como PRIMEIRO campo da área de filtros sempre que estiver disponível.
  const nativeItems = [
    availFilter('q') ? { key: 'q', el: html`
      <div key="q" class="w-64">
        <input class="wa-field w-full px-3 py-2 rounded-md text-[13px]" type="text" value=${q}
          placeholder="nome, telefone ou nº do protocolo" onInput=${(e) => setQ(e.target.value)} />
      </div>` } : null,
    availFilter('status') ? { key: 'status', el: html`
      <div key="status" class="w-[150px]">
        <${OptionListSelect} options=${[{ value: 'aberto', label: 'Aberto' }, { value: 'fechado', label: 'Fechado' }]}
          multiple=${true} value=${asFilterList(status)}
          onChange=${(v) => setStatus(v)} placeholder="Status" float=${true} />
      </div>` } : null,
    availFilter('atendente') ? { key: 'atendente', el: html`
      <div key="atendente" class="w-[170px]">
        <${OptionListSelect}
          options=${users.map((u) => ({ value: String(u.id), label: u.name || `Usuário #${u.id}` }))}
          multiple=${true} value=${asFilterList(assigneeFilter).map(String)}
          onChange=${(v) => setAssigneeFilter(v)} placeholder="Atendente" float=${true} />
      </div>` } : null,
    availFilter('canal') ? { key: 'canal', el: html`
      <div key="canal" class="w-[180px]">
        <${OptionListSelect} options=${channels} multiple=${true}
          value=${asFilterList(attrFilters['canal'])}
          onChange=${(v) => setAttrFilters((s) => { const n = { ...s }; if (v && v.length) n['canal'] = v; else delete n['canal']; return n; })}
          placeholder="Canal" float=${true} />
      </div>` } : null,
    availFilter('nota') ? { key: 'nota', el: html`
      <div key="nota" class="w-[150px]">
        <${OptionListSelect} options=${[5, 4, 3, 2, 1].map((n) => ({ value: String(n), label: `${'★'.repeat(n)} ${n}` }))}
          multiple=${true} value=${asFilterList(notaFilter).map(String)}
          onChange=${(v) => setNotaFilter(v)} placeholder="Avaliação" float=${true} />
      </div>` } : null,
    availFilter('periodo') ? { key: 'periodo', el: html`
      <div key="periodo" class="flex items-center gap-2">
        <span class="text-[12px] text-wa-secondary whitespace-nowrap">Período</span>
        <div class="flex flex-wrap items-center gap-1.5">
          <input type="date" class="wa-field px-2 py-1.5 rounded-md text-[13px]" value=${dateFrom}
            onInput=${(e) => onManualDate(e.target.value, dateTo)} />
          <span class="text-wa-secondary">→</span>
          <input type="date" class="wa-field px-2 py-1.5 rounded-md text-[13px]" value=${dateTo}
            onInput=${(e) => onManualDate(dateFrom, e.target.value)} />
          ${DATE_PRESETS.map(([lbl, d]) => html`<button key=${lbl} onClick=${() => onPresetDate(d, lbl)}
            class="px-2.5 py-1 rounded-md text-[12px] border ${datePreset === lbl ? 'bg-wa-teal text-white border-wa-teal' : 'border-wa-border text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
          <button onClick=${onClearDate}
            class="px-2.5 py-1 rounded-md text-[12px] border ${datePreset === 'tudo' ? 'bg-wa-teal text-white border-wa-teal' : 'border-wa-border text-wa-text hover:bg-wa-hover'}">Tudo</button>
        </div>
      </div>` } : null,
  ].filter(Boolean);

  // Constrói as seções (Nativas → Protocolos → Atendimentos → Contato → Outros) na ordem de
  // filterCategories, mantendo SÓ os campos cuja chave passa em `keep` (predicado de partição).
  // Reusa a mesma montagem; só as seções com ≥1 campo entram. Usado 2x: favoritos e não-favoritos.
  const buildSections = (keep) => filterCategories(filterFields, contactAttrDefs).map((c) => {
    if (c.key === 'nativas') {
      return { label: c.label, els: nativeItems.filter((it) => keep(it.key)).map((it) => it.el) };
    }
    const els = c.attrs
      ? c.attrs.filter((d) => availFilter(`cattr:${d.key}`) && keep(`cattr:${d.key}`))
          .map((d) => renderDynField({ fk: `cattr:${d.key}`, label: d.label, options: d.options }))
      : (c.fields || []).filter((d) => availFilter(`pf:${d.scope}:${d.key}`) && keep(`pf:${d.scope}:${d.key}`))
          .map((d) => renderDynField({ fk: `pf:${d.scope}:${d.key}`, label: d.label, options: d.options }));
    return { label: c.label, els };
  }).filter((s) => s.els.length);

  // Favoritos: sempre visíveis. Não-favoritos: só quando o botão "Filtros" está aberto.
  // Na BARRA a divisão por categorias NÃO aparece (lista plana) — a categorização fica só no
  // painel "Configurar filtros da aba". buildSections dá a ordem (Nativas→…→Outros) e aqui
  // achatamos os campos numa única fila.
  const favEls = buildSections((k) => isFavorite(k)).flatMap((s) => s.els);
  const restEls = buildSections((k) => !isFavorite(k)).flatMap((s) => s.els);
  const hasRest = restEls.length > 0;

  // "Visualização do agrupamento" (origem Pessoal/Equipe + Salvar) e o ⚙ aparecem só quando há uma
  // view REAL (não o fallback). O ⚙ (popover "Configurar filtros da aba") exige poder editar a view.
  const showSettings = !!(activeView && activeView.id != null && activeView.id !== FALLBACK_VIEW.id);

  return html`
    <div>
      <!-- Toolbar unificada (Linha A): tudo à ESQUERDA, em fila — Voltar + título + Kanban/Lista e,
           logo ao lado, ⚙ + contagem + Atualizar + Limpar + Visualização (origem). (Buscar mora na
           barra de filtros.) -->
      <div class="flex flex-wrap items-center gap-2 mb-3">
        ${backAndTitle}
        <div class="inline-flex rounded-lg border border-wa-border overflow-hidden">
          ${[['kanban', 'Kanban'], ['lista', 'Lista']].map(([k, lbl]) => html`
            <button key=${k} onClick=${() => setMode(k)}
              class="px-3 py-1.5 text-[13px] ${mode === k ? 'bg-wa-teal text-white' : 'bg-wa-panel text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <!-- ⚙ = só o checklist "Configurar filtros da aba". Abre para a DIREITA (left-0) e é largo. -->
          ${showSettings && canEditView(activeView) ? html`
            <div ref=${settingsRef} class="relative">
              <button type="button" onClick=${() => setSettingsOpen((o) => !o)}
                title="Configurar filtros da aba"
                class="px-2.5 py-1.5 rounded-md text-[13px] border ${settingsOpen ? 'bg-wa-hover border-wa-teal' : 'border-wa-border hover:bg-wa-hover'} text-wa-text">⚙</button>
              ${settingsOpen ? html`
                <div class="absolute left-0 mt-1 z-30 w-[52rem] max-w-[calc(100vw-2rem)] max-h-[70vh] overflow-y-auto p-3 rounded-lg bg-wa-panel border border-wa-border shadow-lg text-left">
                  <${ViewGroupingConfig} key=${activeView.id} embedded=${true}
                    view=${activeView} filterFields=${filterFields} contactAttrDefs=${contactAttrDefs}
                    apiBase=${apiBase} authHeaders=${authHeaders}
                    onSaved=${() => { appliedViewRef.current = null; loadViews(); }} />
                </div>` : null}
            </div>` : null}
          ${(mode === 'kanban' ? !kanbanLoading : !loading) ? html`<span class="text-[12px] text-wa-secondary">${shownTotal} protocolo${shownTotal === 1 ? '' : 's'}${shownPartial ? '+' : ''}</span>` : null}
          <!-- Atualiza lista E metadados: depois do split reload()/loadMeta(), só o reload traria as
               linhas e deixaria defs/atendentes/canais parados na tela. NOTA: nada de crase neste
               comentário — ele vive DENTRO do template do htm e uma crase encerra o template (bug 1.15.0). -->
          <button onClick=${() => { loadMeta(); reload(); }} class="px-3 py-1.5 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover">Atualizar</button>
          ${hasViewFilters ? html`<button onClick=${clearFilters}
            class="px-3 py-1.5 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover">Limpar filtros</button>` : null}
          <!-- Visualização do agrupamento (origem Pessoal/Equipe + Salvar) — na toolbar, ao lado de
               Atualizar/Limpar. Só quando há uma view real. -->
          ${showSettings ? html`
            <div class="inline-flex flex-wrap items-center gap-2">
              <span class="text-[12px] text-wa-secondary">Visualização:</span>
              <label class="inline-flex items-center gap-1.5 cursor-pointer text-[13px] text-wa-text">
                <input type="radio" name=${`src-${activeView.id}`} checked=${!!(activeView.pref && activeView.pref.use_personal)} onChange=${() => switchSource(true)} /> Pessoal
              </label>
              <label class="inline-flex items-center gap-1.5 cursor-pointer text-[13px] text-wa-text">
                <input type="radio" name=${`src-${activeView.id}`} checked=${!(activeView.pref && activeView.pref.use_personal)} onChange=${() => switchSource(false)} /> Equipe
              </label>
              ${(() => {
                // Em Equipe sem permissão de editar a view não dá pra salvar o padrão da equipe:
                // botão desabilitado + dica (title) p/ trocar pra Pessoal. (Alternar o rádio é sempre livre.)
                const usePersonal = !!(activeView.pref && activeView.pref.use_personal);
                const blockedTeam = !usePersonal && !canEditView(activeView);
                return html`<button onClick=${saveViewFilters} disabled=${savingPref || blockedTeam}
                  title=${blockedTeam ? 'Sem permissão para salvar a visualização da equipe — selecione Pessoal' : ''}
                  class="px-3 py-1.5 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover disabled:opacity-50">Salvar visualização de filtros</button>`;
              })()}
              ${prefMsg ? html`<span class="text-[12px] ${prefMsg.includes('Falha') ? 'text-red-500' : 'text-wa-teal'}">${prefMsg}</span>` : null}
            </div>` : null}
        </div>
      </div>

      <!-- Linha B — barra de filtros slim (rótulos inline via placeholder). FAVORITOS sempre;
           demais atrás do botão "Filtros". Buscar/Atualizar/Limpar/contagem migraram p/ a toolbar. -->
      ${(favEls.length || hasRest) ? html`
      <div class="mb-3 p-3 rounded-lg bg-wa-panel border border-wa-border">
        ${favEls.length ? html`<div class="flex flex-wrap items-center gap-3">${favEls}</div>` : null}
        <!-- Botão "Filtros" + campos NÃO-favoritos na MESMA linha flex-wrap: ao abrir, os campos
             começam ao LADO do botão e só quebram para baixo quando atingem o fim da área. -->
        ${hasRest ? html`
          <div class="${favEls.length ? 'mt-3 pt-3 border-t border-wa-border' : ''} flex flex-wrap items-center gap-3">
            <button type="button" onClick=${() => setFiltersOpen((o) => !o)}
              class="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover">
              <span class="text-wa-secondary w-3">${filtersOpen ? '▾' : '▸'}</span>Filtros
            </button>
            ${filtersOpen ? restEls : null}
          </div>` : null}
      </div>` : null}

      <!-- Abas "Agrupar por" — só no Kanban (agrupamento não se aplica à Lista); aparece
           mesmo quando vazio, p/ não perder a aba selecionada. -->
      ${mode === 'kanban' ? tabBar : null}

      <!-- Kanban e Lista têm ciclos de carga distintos: o Kanban carrega as COLUNAS
           (e cada coluna pagina sozinha); a Lista pagina a si mesma. -->
      ${mode === 'kanban'
        ? (kanbanLoading ? html`<div class="text-[13px] text-wa-secondary p-4">Carregando…</div>`
           : (!orderedColumns.length || orderedColumns.every((c) => !c.total))
             ? html`<div class="text-[13px] text-wa-secondary p-4">Nenhum protocolo.${hasViewFilters ? html` Esta aba tem filtros pré-determinados — <button onClick=${clearFilters} class="text-wa-teal hover:underline">limpar filtros</button> para ver todos.` : null}</div>`
             : kanbanView)
        : loading ? html`<div class="text-[13px] text-wa-secondary p-4">Carregando…</div>`
          : rows.length === 0 ? html`<div class="text-[13px] text-wa-secondary p-4">Nenhum protocolo.${hasViewFilters ? html` Esta aba tem filtros pré-determinados — <button onClick=${clearFilters} class="text-wa-teal hover:underline">limpar filtros</button> para ver todos.` : null}</div>`
          : listaView}

      <!-- Fim da Lista: marcador usado p/ achar o container que rola + estado do lote. -->
      ${mode === 'lista' && !loading ? html`<div ref=${listEndRef} class="h-2"></div>` : null}
      ${mode === 'lista' && loadingMore
        ? html`<div class="text-[12px] text-wa-secondary p-2 text-center">Carregando mais…</div>`
        : (mode === 'lista' && !loading && hasMore
            ? html`<div class="text-[12px] text-wa-secondary p-2 text-center">Role para carregar mais (${rows.length}/${totalCount})</div>`
            : null)}

      ${detail ? html`<${DetailModal} key=${detail.protocolo && detail.protocolo.id}
        data=${detail} fieldDefs=${atendDefs}
        protoDefs=${cols} warning=${detailWarning} api=${api} canEdit=${canEdit}
        defaultAssignee=${currentUser && currentUser.id}
        defaultAssigneeName=${currentUser && (currentUser.name || currentUser.email)}
        onClose=${closeDetail}
        onChanged=${reload} onFinalize=${finalizeProtocolo} />` : null}
    </div>`;
}

// Card do kanban: draggable (altera estado ao soltar) + clique abre o detalhe. A CAPA
// mostra só os dados próprios do protocolo (cliente, datas, atendente, status) — os
// rótulos do plugin ficam no DETALHE (modal), junto da atendimento a que pertencem.
function KanbanCard({ row, draggedRef, dragRef, onClearDrop, onOpen, canDrag = true }) {
  return html`
    <div draggable=${canDrag}
      onDragStart=${(e) => {
        if (!canDrag) { e.preventDefault(); return; }
        draggedRef.current = true; dragRef.current = row;
        try { e.dataTransfer.setData('text/plain', String(row.id)); e.dataTransfer.effectAllowed = 'move'; } catch (_) { /* ignore */ }
      }}
      onDragEnd=${() => { dragRef.current = null; if (onClearDrop) onClearDrop(); setTimeout(() => { draggedRef.current = false; }, 0); }}
      onClick=${() => { if (draggedRef.current) { draggedRef.current = false; return; } onOpen(); }}
      class="p-3 rounded-lg border border-wa-border bg-wa-panel hover:border-wa-teal/50 ${canDrag ? 'cursor-grab active:cursor-grabbing' : 'cursor-pointer'} transition-colors"
      title=${canDrag ? 'Arraste para mover · clique para abrir' : 'Clique para abrir'}>
      <div class="font-medium text-wa-text text-[13px] truncate">${row.contact_name || row.contact_phone || '—'}</div>
      ${row.contact_phone ? html`<div class="text-[12px] text-wa-secondary truncate">${row.contact_phone}</div>` : null}
      <div class="text-[12px] text-wa-secondary mt-1">Início: ${fmtTs(row.opened_at)}</div>
      ${row.closed_at ? html`<div class="text-[12px] text-wa-secondary">Fim: ${fmtTs(row.closed_at)}</div>` : null}
      <div class="flex items-center justify-between gap-2 mt-1">
        <span class="text-[12px] ${row.assignee_name ? 'text-wa-text' : 'text-wa-secondary'} truncate">${row.assignee_name || 'Não atribuído'}</span>
        <div class="flex items-center gap-1 shrink-0">
          ${row.avaliacao ? html`<span class="px-1.5 py-0.5 rounded-full text-[10px] bg-amber-500/15 text-amber-600" title="Avaliação do cliente">★ ${row.avaliacao.nota}</span>` : null}
          <span class="px-1.5 py-0.5 rounded-full text-[10px] ${row.status === 'aberto' ? 'bg-wa-teal/15 text-wa-teal' : 'bg-wa-hover text-wa-secondary'}">${row.status === 'aberto' ? 'Aberto' : 'Fechado'}</span>
        </div>
      </div>
    </div>`;
}

// Detalhe do protocolo. Topo = form EDITÁVEL dos campos do protocolo (OBS fixo +
// extras, normais e obrigatórios), pré-preenchido; "Salvar" persiste parcial (fechar
// depois) e "Finalizar protocolo" só habilita com os obrigatórios preenchidos. Se o
// protocolo já está FECHADO, os valores aparecem read-only (sem ações). A tabela de
// atendimentos (histórico, read-only) fica abaixo.
function DetailModal({ data, fieldDefs = [], protoDefs = [], warning = '',
                      defaultAssignee = null, defaultAssigneeName = null, api, canEdit = true, onClose, onChanged, onFinalize }) {
  const at = data.protocolo || {};
  const atendimentos = data.atendimentos || [];
  const fechado = at.status === 'fechado';                  // status REAL (rótulo do topo)
  // Form read-only quando fechado OU sem permissão de editar → sem Salvar/Finalizar.
  const readOnly = fechado || !canEdit;

  // Estado local dos campos do protocolo, pré-preenchido (extras — obs incluso — em
  // at.fields; o rótulo Atendente vem do assignee nativo). Editável enquanto aberto; serve
  // de fonte também p/ a visão read-only.
  const hasSavedAssignee = at.assignee_user_id != null && String(at.assignee_user_id).trim() !== '';
  const shouldSeedCurrentAssignee = !readOnly && !hasSavedAssignee && defaultAssignee != null;
  const showAssigneeSeedNotice = protoDefs.some((d) => d.type === 'atendente') && shouldSeedCurrentAssignee;
  // Atendente: mantém o assignee salvo quando existir; só usa o usuário conectado como
  // sugestão inicial quando o protocolo ainda não tem atendente gravado (ver proto_fields.js).
  const [vals, setVals] = useState(() => seedProtocolValues(protoDefs, at, { defaultAssignee, readOnly }));

  // Rede de segurança da corrida (plano 88 · C0): se `protoDefs` chegar DEPOIS do mount
  // (o await do openDetail caiu no catch, config de campos editada noutra aba, um call
  // site novo que não passe pelo openDetail), completa o estado. `mergeSeed` só preenche
  // chave AUSENTE — não apaga o que o operador digitou nem ressuscita campo limpo de
  // propósito, e devolve a mesma referência quando não há nada a somar (sem re-render).
  useEffect(() => {
    setVals((v) => mergeSeed(v, seedProtocolValues(protoDefs, at, { defaultAssignee, readOnly })));
    // `data` (e não `at`) na dependência: `at` é derivado dela e ganharia identidade
    // nova a cada render no caso degenerado `data.protocolo == null`.
  }, [protoDefs, data, defaultAssignee, readOnly]);
  const [saving, setSaving] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [msg, setMsg] = useState(null);          // {text, error}
  const [linkCopied, setLinkCopied] = useState(false);
  const [generalConfig, setGeneralConfig] = useState(null);

  useEffect(() => {
    let alive = true;
    api.http.get('/general-config').then((d) => {
      if (alive && d && d.ok) setGeneralConfig(d.data || {});
    }).catch(() => {});
    return () => { alive = false; };
  }, [api]);

  // Copia o link compartilhável deste protocolo (/protocolos?detail=<id>) — plano 24.
  const copyLink = () => copyText(`${location.origin}/protocolos?detail=${at.id}`,
    () => { setLinkCopied(true); setTimeout(() => setLinkCopied(false), 2000); });

  const missing = protoDefs.filter((d) => d.required && !isFilledProto(d, vals[d.key]));

  // PUT parcial dos campos do protocolo (obrigatório só é exigido ao FECHAR).
  // Via api.http: status-aware + toast de 403 (some o falso "sucesso").
  const putFields = (opts = {}) => api.http.put(`/protocolos/${at.id}/fields`, { fields: vals, ...opts });

  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      const j = await putFields();
      if (j && j.ok) { setMsg({ text: 'Campos salvos.', error: false }); if (onChanged) onChanged(); }
      else setMsg({ text: (j && j.error) || 'Falha ao salvar.', error: true });
    } catch (_) { setMsg({ text: 'Falha ao salvar.', error: true }); }
    finally { setSaving(false); }
  };

  const finalize = async () => {
    setFinalizing(true); setMsg(null);
    const autoAssign = !generalConfig || generalConfig.auto_assign_conversation_on_close !== false;
    try { await putFields({ propagate_assignee_to_conversations: autoAssign }); } // persiste antes de fechar
    catch (_) { /* o close revalida os obrigatórios e devolve erro se faltar */ }
    let res;
    try { res = await onFinalize(at.id); }
    catch (_) { res = { ok: false, error: 'Falha ao finalizar.' }; }
    // Sucesso → o pai fecha o modal (setDetail(null)); só tratamos o erro aqui.
    if (res && res.ok === false) { setMsg({ text: res.error || 'Falha ao finalizar.', error: true }); setFinalizing(false); }
  };

  // Clicar numa atendimento (ciclo) → resolve a âncora (1ª mensagem do ciclo) no backend
  // e abre o chat rolando ATÉ aquele ponto exato (permalink ?message=<_id>). Se a
  // âncora falhar, abre a atendimento mesmo assim (sem o scroll fino).
  const openAtend = async (c) => {
    if (!c) return;
    let atendId = c.conversation_id;
    let msgId = null;
    try {
      const r = await fetch(`${api.apiBase}/atendimentos/${c.id}/anchor`, { headers: api.services.authHeaders() });
      const d = await r.json();
      if (d && d.ok && d.data) {
        if (d.data.conversation_id != null) atendId = d.data.conversation_id;
        if (d.data.message_id != null) msgId = d.data.message_id;
      }
    } catch (_) { /* fallback: abre a atendimento sem âncora de mensagem */ }
    onClose();
    openConversation(atendId, msgId);
  };

  // Visão read-only (protocolo fechado): só os campos com valor (checkbox sempre exibe).
  const readOnlyInfo = protoDefs.filter((d) => d.type === 'checkbox' || isFilledProto(d, vals[d.key]));

  return html`
    <div class="fixed inset-0 bg-black/50 z-[70] flex items-center justify-center p-4">
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-2xl w-full p-6 max-h-[85vh] overflow-auto">
        <div class="flex items-center justify-between mb-3 gap-2">
          <h2 class="text-base font-semibold text-wa-text truncate">${at.contact_name || at.contact_phone || 'Protocolo'}</h2>
          <div class="flex items-center gap-1 shrink-0">
            <button onClick=${copyLink} title="Copiar link deste protocolo"
              class="px-2 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors">
              ${linkCopied ? '✓ Link copiado' : 'Copiar link'}
            </button>
            <button onClick=${onClose} class="text-wa-secondary hover:text-wa-text text-xl leading-none px-1">×</button>
          </div>
        </div>
        <div class="text-[12px] text-wa-secondary mb-3">
          Início: ${fmtTs(at.opened_at)}${at.closed_at ? ` · Fim: ${fmtTs(at.closed_at)}` : ''} · ${fechado ? 'Fechado' : 'Aberto'}
        </div>

        ${warning ? html`
          <div class="mb-3 px-3 py-2.5 rounded-md bg-amber-500/15 border border-amber-500 text-amber-700 text-[13px] font-semibold">
            ${warning}
          </div>` : null}

        ${readOnly ? html`
          <div class="mb-4 p-3 rounded-lg bg-wa-panel border border-wa-border">
            <div class="text-wa-iconActive text-[13px] font-semibold mb-1.5">Dados do protocolo</div>
            <div class="flex flex-wrap gap-x-5 gap-y-1 text-[12px] mb-2">
              <div><span class="text-wa-secondary">Aberto por:</span> <span class="text-wa-text">${at.opened_by_name || '—'}</span></div>
              <div><span class="text-wa-secondary">Fechado por:</span> <span class="text-wa-text">${fechado ? (at.assignee_name || '—') : '—'}</span></div>
            </div>
            ${readOnlyInfo.length ? html`
              <div class="flex flex-wrap gap-x-5 gap-y-1 text-[12px]">
                ${readOnlyInfo.map((d) => html`<div key=${d.key}>
                  <span class="text-wa-secondary">${d.label}:</span> <span class="text-wa-text">${d.type === 'atendente' ? (at.assignee_name || '—') : fmtCell(vals[d.key], d)}</span>
                </div>`)}
              </div>` : html`<div class="text-[12px] text-wa-secondary">Sem dados preenchidos.</div>`}
          </div>`
        : html`
          <div class="mb-4 p-3 rounded-lg bg-wa-panel border border-wa-border">
            <div class="text-wa-iconActive text-[13px] font-semibold mb-2">Dados do protocolo</div>
            <div class="flex flex-wrap gap-x-5 gap-y-1 text-[12px] mb-3">
              <div><span class="text-wa-secondary">Aberto por:</span> <span class="text-wa-text">${at.opened_by_name || '—'}</span></div>
              <div><span class="text-wa-secondary">Fechado por:</span> <span class="text-wa-text">${fechado ? (at.assignee_name || '—') : '—'}</span></div>
            </div>
            ${showAssigneeSeedNotice ? html`
              <div class="mb-3 px-3 py-2.5 rounded-md bg-wa-teal/10 border border-wa-teal/40 text-wa-teal text-[13px]">
                Este protocolo ainda não tinha atendente — preenchemos com o seu usuário. Troque se necessário.
              </div>` : null}
            <div class="space-y-3">
              ${protoDefs.map((d) => {
                // O rótulo "atendente" precisa de um fallbackUser para o AttendantSelect
                // conseguir EXIBIR um id que não esteja na lista de atendentes atribuíveis
                // (ex.: usuário logado sem conversation.read, ou fora da lista). Sem ele o
                // select cai no placeholder "Não atribuído" mesmo com o valor semeado.
                // Cobre os dois casos: assignee JÁ salvo e o seed do usuário logado.
                const fallbackUser = hasSavedAssignee
                  ? { id: at.assignee_user_id, name: at.assignee_name }
                  : (shouldSeedCurrentAssignee ? { id: defaultAssignee, name: defaultAssigneeName } : null);
                const def = (d.type === 'atendente' && fallbackUser)
                  ? { ...d, fallbackUser }
                  : d;
                return html`<${LabeledField} key=${d.key} def=${def}
                  value=${vals[d.key]}
                  onChange=${(v) => setVals((s) => ({ ...s, [d.key]: v }))} />`;
              })}
            </div>
            ${missing.length ? html`
              <div class="mt-4 px-3 py-2.5 rounded-md bg-red-500/15 border border-red-500 text-red-500 text-[14px] font-semibold">
                Preencha os campos obrigatórios para finalizar: ${missing.map((d) => d.label || d.key).join(', ')}.
              </div>` : null}
            ${msg ? html`
              <div class="mt-3 px-3 py-2 rounded-md text-[13px] ${msg.error ? 'bg-red-500/15 border border-red-500 text-red-500 font-semibold' : 'bg-wa-teal/10 text-wa-teal'}">${msg.text}</div>` : null}
            <div class="flex gap-2 mt-4">
              <button onClick=${save} disabled=${saving || finalizing}
                class="px-4 py-2 rounded-lg bg-wa-panel border border-wa-border text-wa-text hover:bg-wa-hover disabled:opacity-50 text-[14px]">
                ${saving ? 'Salvando…' : 'Salvar'}</button>
              <button onClick=${finalize} disabled=${missing.length > 0 || finalizing || saving}
                class="flex-1 px-4 py-2 rounded-lg bg-wa-teal text-white hover:opacity-90 disabled:opacity-50 text-[14px] font-medium">
                ${finalizing ? 'Finalizando…' : 'Finalizar protocolo'}</button>
            </div>
          </div>`}

        ${at.avaliacao ? html`
          <div class="mb-4 p-3 rounded-lg bg-wa-panel border border-wa-border">
            <div class="text-wa-iconActive text-[13px] font-semibold mb-1.5">Avaliação do cliente</div>
            <div class="flex items-center gap-2 text-[13px]">
              <span class="text-amber-500 text-[15px] tracking-wide">${'★'.repeat(at.avaliacao.nota || 0)}${'☆'.repeat(Math.max(0, 5 - (at.avaliacao.nota || 0)))}</span>
              <span class="text-wa-text font-medium">${at.avaliacao.nota}/5</span>
              ${at.avaliacao.answered_at ? html`<span class="text-wa-secondary text-[12px]">· ${fmtTs(at.avaliacao.answered_at)}</span>` : null}
            </div>
            ${at.avaliacao.sugestao ? html`
              <div class="mt-2 text-[13px] text-wa-text whitespace-pre-wrap"><span class="text-wa-secondary">Sugestão:</span> ${at.avaliacao.sugestao}</div>` : null}
          </div>` : null}

        <div class="text-wa-iconActive text-[13px] font-semibold mb-2">Atendimentos</div>
        <div class="text-[12px] text-wa-secondary mb-2">Clique num atendimento para abri-lo no chat.</div>
        <${AtendimentosTable} atendimentos=${atendimentos} fieldDefs=${fieldDefs}
          storageKey="whatsbot_proto_atend_cols_modal" onRowClick=${openAtend} />
      </div>
    </div>`;
}

// Diálogo de confirmação genérico (usado pelo drag que altera valor e pelo excluir
// visualização). Descreve a ação e só resolve true ao confirmar.
function ConfirmDialog({ message, onOk, onCancel, okLabel = 'Confirmar', danger = false }) {
  return html`
    <div class="fixed inset-0 bg-black/50 z-[80] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-md w-full p-6">
        <div class="text-[14px] text-wa-text mb-4">${message}</div>
        <div class="flex justify-end gap-2">
          <button onClick=${onCancel}
            class="px-4 py-2 rounded-lg bg-wa-panel border border-wa-border text-wa-text hover:bg-wa-hover text-[14px]">Cancelar</button>
          <button onClick=${onOk}
            class="px-4 py-2 rounded-lg ${danger ? 'bg-red-500' : 'bg-wa-teal'} text-white hover:opacity-90 text-[14px] font-medium">${okLabel}</button>
        </div>
      </div>
    </div>`;
}

// Painel recolhível (acima da barra, no Kanban e na Lista) com a configuração GERAL do
// AGRUPAMENTO: quais TIPOS de filtro existem nesta aba (available_filters da view). Só é
// renderizado para quem pode editar a visualização. Os VALORES pré-determinados NÃO vivem
// mais aqui — são salvos a partir dos filtros ATUAIS da barra pelos botões "Salvar filtros
// pessoal/equipe" acima da barra. Montar com key=${view.id} reinicializa o estado por aba.
// `embedded=true` (dentro do popover ⚙ do topo): pula a casca própria (bg-wa-panel/border) e o
// botão de colapso, renderizando o corpo sempre aberto — o popover já é o colapso.
function ViewGroupingConfig({ view, filterFields, contactAttrDefs, apiBase, authHeaders, onSaved, embedded = false }) {
  // Categorias do checklist (SÓ apresentação — available_filters continua array plano de chaves):
  // Nativas + campos de PROTOCOLO (pf:protocolo:) + de ATENDIMENTO (pf:atendimento:) + de CONTATO
  // (cattr:). "Outros" (defensivo) captura escopos de campo desconhecidos p/ nada sumir do checklist.
  // Chaves nativas do checklist (lista fixa — a barra ao vivo renderiza widgets próprios p/ elas).
  const NATIVE_ITEMS = [
    ['status', 'Status'], ['atendente', 'Atendente'], ['q', 'Buscar'], ['periodo', 'Período'],
    ['canal', 'Canal'], ['nota', 'Avaliação'],
  ];
  // Derivado da fonte única filterCategories() → pares [key,label] que o `cbox` espera
  // (`pf:${scope}:${key}` p/ campos, `cattr:${key}` p/ contato). Comportamento inalterado.
  const CATEGORIES = filterCategories(filterFields, contactAttrDefs).map((c) => ({
    key: c.key, label: c.label,
    items: c.key === 'nativas' ? NATIVE_ITEMS
      : c.attrs ? c.attrs.map((d) => [`cattr:${d.key}`, d.label])
      : (c.fields || []).map((d) => [`pf:${d.scope}:${d.key}`, d.label]),
  })).filter((c) => c.items.length);
  // Tipos de filtro que podem existir na aba (flatten das categorias). null na view = TODOS.
  const ALL_FILTER_KEYS = CATEGORIES.flatMap((c) => c.items.map(([k]) => k));
  const initAvail = (view && Array.isArray(view.available_filters)) ? view.available_filters : null;
  const initFav = (view && Array.isArray(view.favorite_filters)) ? view.favorite_filters : [];
  const [open, setOpen] = useState(false);
  const [availSet, setAvailSet] = useState(() => new Set(initAvail == null ? ALL_FILTER_KEYS : initAvail));
  // Favoritos: sempre visíveis na barra (não recolhíveis pelo botão "Filtros"). Subconjunto de availSet.
  const [favSet, setFavSet] = useState(() => new Set(initFav));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');
  const [okMsg, setOkMsg] = useState('');
  const [catView, setCatView] = useState('todas');  // categoria ativa do checklist ('todas' = agrupado com cabeçalhos)
  const isAvail = (key) => availSet.has(key);
  // Desmarcar a disponibilidade tira o favorito junto (favorito precisa estar disponível).
  const toggleAvail = (key) => { setOkMsg(''); setAvailSet((s) => {
    const n = new Set(s); if (n.has(key)) { n.delete(key); setFavSet((f) => { const g = new Set(f); g.delete(key); return g; }); } else n.add(key); return n;
  }); };
  const isFav = (key) => favSet.has(key);
  // Marcar favorito garante disponível (senão não apareceria na barra). Desmarcar só tira o favorito.
  const toggleFav = (key) => { setOkMsg(''); setFavSet((f) => {
    const g = new Set(f);
    if (g.has(key)) { g.delete(key); }
    else { g.add(key); setAvailSet((s) => { const n = new Set(s); n.add(key); return n; }); }
    return g;
  }); };

  const save = async () => {
    setErr(''); setOkMsg(''); setSaving(true);
    try {
      // available_filters: todos habilitados → null (todos, à prova de futuro); senão a lista.
      // O body NÃO leva filters → o backend preserva os VALORES pré-determinados da equipe.
      const enabled = ALL_FILTER_KEYS.filter((k) => availSet.has(k));
      const available_filters = enabled.length === ALL_FILTER_KEYS.length ? null : enabled;
      // favorite_filters: subconjunto disponível marcado como favorito; vazio → null (nenhum).
      const favorites = ALL_FILTER_KEYS.filter((k) => favSet.has(k) && availSet.has(k));
      const favorite_filters = favorites.length ? favorites : null;
      const r = await fetch(`${apiBase}/kanban-views/${view.id}`, {
        method: 'PUT',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ available_filters, favorite_filters }),
      });
      const j = await r.json().catch(() => ({}));
      if (!(j && j.ok)) { setErr((j && j.error) || 'Falha ao salvar.'); setSaving(false); return; }
      setSaving(false); setOkMsg('Configuração salva.');
      onSaved && onSaved();
    } catch (_) { setErr('Falha ao salvar.'); setSaving(false); }
  };

  // Renderiza os checkboxes de uma lista [[key,label],...] (reusado nas duas vistas do checklist).
  // Cada item: caixa de disponibilidade + rótulo + estrela dourada de FAVORITO (sempre visível
  // na barra). A estrela só faz sentido quando disponível — clicar nela auto-habilita o filtro.
  const cbox = (items) => items.map(([key, lbl]) => html`
    <span key=${key} class="inline-flex items-center gap-1.5">
      <label class="inline-flex items-center gap-1.5 cursor-pointer">
        <input type="checkbox" checked=${isAvail(key)} onChange=${() => toggleAvail(key)} /> ${lbl}
      </label>
      <button type="button" onClick=${() => toggleFav(key)}
        title=${isFav(key) ? 'Filtro favorito (sempre visível) — clique para remover' : 'Marcar como favorito (sempre visível na barra)'}
        class="leading-none text-[15px] ${isFav(key) ? 'text-amber-400' : 'text-wa-secondary hover:text-amber-400'}">${isFav(key) ? '★' : '☆'}</button>
    </span>`);

  // Escopo dos botões "Selecionar todos"/"Limpar": SÓ a categoria ativa (aba 'todas' = tudo).
  // Numa categoria, marcar/limpar não mexe nas seleções das OUTRAS categorias.
  const activeCat = catView === 'todas' ? null : CATEGORIES.find((c) => c.key === catView);
  const scopeKeys = activeCat ? activeCat.items.map(([k]) => k) : ALL_FILTER_KEYS;
  const scopeLabel = activeCat ? ` de ${activeCat.label}` : '';
  const selectScope = () => { setOkMsg(''); setAvailSet((s) => new Set([...s, ...scopeKeys])); };
  const clearScope = () => { setOkMsg('');
    setAvailSet((s) => { const n = new Set(s); scopeKeys.forEach((k) => n.delete(k)); return n; });
    setFavSet((f) => { const g = new Set(f); scopeKeys.forEach((k) => g.delete(k)); return g; });  // sem disponibilidade → sem favorito
  };

  // Corpo do checklist (idêntico nos dois modos). Em embedded não leva a borda/padding-topo da casca.
  const body = html`
      <div class="${embedded ? '' : 'px-3 pb-3 pt-3 border-t border-wa-border'}">
        <span class="block text-[12px] text-wa-secondary font-medium mb-1">Filtros disponíveis nesta aba</span>
        <div class="text-[11px] text-wa-secondary mb-2">Só os tipos de filtro marcados aparecem na barra de filtros deste agrupamento.</div>
        <!-- Seletor de categoria: "Todas" mostra os grupos com cabeçalho; cada aba isola uma categoria. -->
        <div class="flex flex-wrap items-center gap-1.5 mb-3">
          ${[['todas', 'Todas'], ...CATEGORIES.map((c) => [c.key, c.label])].map(([k, lbl]) => html`
            <button key=${k} type="button" onClick=${() => setCatView(k)}
              class="px-2.5 py-1 rounded-md text-[12px] border ${catView === k ? 'bg-wa-teal text-white border-wa-teal' : 'border-wa-border text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
        </div>
        ${catView === 'todas'
          ? CATEGORIES.map((c, i) => html`
            <div key=${c.key} class="${i > 0 ? 'mt-3 pt-3 border-t border-wa-border' : ''}">
              <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-1.5">${c.label}</div>
              <div class="flex flex-wrap gap-x-4 gap-y-1.5 text-[13px] text-wa-text">${cbox(c.items)}</div>
            </div>`)
          : html`<div class="flex flex-wrap gap-x-4 gap-y-1.5 text-[13px] text-wa-text">
              ${cbox((CATEGORIES.find((c) => c.key === catView) || { items: [] }).items)}
            </div>`}
        ${err ? html`<div class="mt-3 px-3 py-2 rounded-md text-[13px] bg-red-500/15 border border-red-500 text-red-500 font-semibold">${err}</div>` : null}
        <div class="flex flex-wrap items-center gap-2 mt-3">
          <button type="button" onClick=${selectScope}
            class="px-3 py-2 rounded-lg bg-wa-hover text-wa-text border border-wa-border hover:opacity-90 text-[13px]">Selecionar todos${scopeLabel}</button>
          <button type="button" onClick=${clearScope}
            class="px-3 py-2 rounded-lg bg-wa-hover text-wa-text border border-wa-border hover:opacity-90 text-[13px]">Limpar${scopeLabel}</button>
          ${okMsg ? html`<span class="text-[12px] text-wa-teal ml-auto">${okMsg}</span>` : null}
          <button onClick=${save} disabled=${saving}
            class="${okMsg ? '' : 'ml-auto'} px-4 py-2 rounded-lg bg-wa-teal text-white hover:opacity-90 disabled:opacity-50 text-[13px] font-medium">${saving ? 'Salvando…' : 'Salvar configuração da aba'}</button>
        </div>
      </div>`;

  // Embedded (popover): título simples + corpo sempre aberto. Standalone: casca própria + colapso.
  if (embedded) {
    return html`
      <div>
        <div class="text-[12px] text-wa-secondary font-medium mb-2">⚙ Configurar filtros da aba</div>
        ${body}
      </div>`;
  }
  return html`
    <div class="mb-3 rounded-lg bg-wa-panel border border-wa-border">
      <button type="button" onClick=${() => setOpen((o) => !o)}
        class="w-full flex items-center gap-2 px-3 py-2 text-[13px] text-wa-text hover:bg-wa-hover rounded-lg">
        <span class="text-wa-secondary w-3">${open ? '▾' : '▸'}</span>
        <span>⚙ Configurar filtros da aba</span>
      </button>
      ${open ? body : null}
    </div>`;
}

// Editor de visualização (criar/editar uma aba de "Agrupar por"). Cuida só dos METADADOS:
// Nome, Quem pode ver (ACL de grupos/usuários) e Agrupar por (nativos + atributos lista).
// Os TIPOS de filtro da aba saíram para o painel "Configurar filtros da aba"
// (ViewGroupingConfig, acima da barra), e os VALORES pré-determinados para os botões
// "Salvar visualização pessoal/equipe". Salva via POST/PUT /kanban-views SEM tocar em
// filters/available_filters (o backend preserva no update e usa defaults no create).
function ViewEditorModal({ view, groupFields, users, roles, canTeam, currentUser, api, onSaved, onCancel }) {
  const editing = !!(view && view.id != null);
  // Pode editar os METADADOS + filtros de equipe? Criar (view=null) sempre pode (vira pessoal).
  const canEditMeta = !view ? true
    : (canTeam || (view.scope === 'personal'
        && (!currentUser || view.owner_user_id === currentUser.id)));
  const protoGroupFields = (groupFields || []).filter((d) => d.scope === 'protocolo');
  const atendGroupFields = (groupFields || []).filter((d) => d.scope === 'atendimento');
  const initGroup = () => {
    if (!view) return 'status';
    if (view.group_by === 'pfield') return `pfield:${view.group_field_scope || ''}:${view.group_attr_key || ''}`;
    if (view.group_by === 'attr') return 'status';  // legado (atributo de conversa) → cai em Status
    return view.group_by;
  };
  // ACL "Quem pode ver": grupos (roles) + usuários (3 estados: padrão/incluir/excluir).
  const initRoleKeys = (view && Array.isArray(view.visibility_roles)) ? view.visibility_roles : [];
  const initInc = (view && Array.isArray(view.visibility_users_include)) ? view.visibility_users_include : [];
  const initExc = (view && Array.isArray(view.visibility_users_exclude)) ? view.visibility_users_exclude : [];
  // Equipe LEGADO (view team sem ACL): preservar "todos veem" ao salvar sem selecionar nada.
  const wasLegacyTeamAll = editing && view.scope === 'team' && !initRoleKeys.length && !initInc.length;

  const [name, setName] = useState((view && view.name) || '');
  const [visTab, setVisTab] = useState('grupos');                  // 'grupos' | 'usuarios'
  const [visRoles, setVisRoles] = useState(() => new Set(initRoleKeys.map(String)));
  const [userStates, setUserStates] = useState(() => {
    const m = {};
    initInc.forEach((id) => { m[String(id)] = 'include'; });
    initExc.forEach((id) => { m[String(id)] = 'exclude'; });
    return m;
  });
  const [groupSel, setGroupSel] = useState(initGroup());
  const [dateMode, setDateMode] = useState((view && view.group_date_mode) || 'faixas');
  const [dateFrom, setDateFrom] = useState((view && view.group_date_from) || '');
  const [dateTo, setDateTo] = useState((view && view.group_date_to) || '');
  const [dateGrain, setDateGrain] = useState((view && view.group_date_grain) || 'dia');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');

  // ACL "Quem pode ver": grupos + usuários (3 estados). scope é DERIVADO (compartilha → team).
  const toggleRole = (key) => setVisRoles((s) => {
    const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n;
  });
  const setUserState = (uid, st) => setUserStates((s) => {
    const n = { ...s }; if (st === 'padrao') delete n[String(uid)]; else n[String(uid)] = st; return n;
  });
  const userState = (uid) => userStates[String(uid)] || 'padrao';
  const aclBody = () => {
    const roleArr = [...visRoles];
    const includeIds = Object.keys(userStates).filter((k) => userStates[k] === 'include').map(Number);
    const excludeIds = Object.keys(userStates).filter((k) => userStates[k] === 'exclude').map(Number);
    const shared = roleArr.length > 0 || includeIds.length > 0;
    const scope = shared ? 'team' : (wasLegacyTeamAll ? 'team' : 'personal');
    return { scope, visibility_roles: roleArr,
             visibility_users_include: includeIds, visibility_users_exclude: excludeIds };
  };

  const save = async () => {
    setErr('');
    const isPf = groupSel.startsWith('pfield:');
    const pfParts = isPf ? groupSel.split(':') : [];   // ['pfield', scope, key]
    const gb = isPf ? 'pfield' : groupSel;
    const gfs = isPf ? (pfParts[1] || null) : null;
    const gak = isPf ? (pfParts[2] || null) : null;
    const acl = aclBody();  // { scope derivado, visibility_roles, users_include, users_exclude }
    if (!name.trim()) { setErr('Informe um nome para a visualização.'); return; }
    if (gb === 'pfield' && (!gfs || !gak)) { setErr('Selecione um campo de opção para agrupar.'); return; }
    if (acl.scope === 'team' && !canTeam) { setErr('Sem permissão para compartilhar (visualização de equipe).'); return; }
    setSaving(true);
    try {
      // Só METADADOS. O body NÃO leva filters/available_filters → o backend preserva no
      // update e usa defaults ({}/null=todos) no create. Os TIPOS de filtro vivem no painel
      // "Configurar filtros da aba" (ViewGroupingConfig) e os VALORES nos botões "Salvar
      // filtros pessoal/equipe", acima da barra de filtros.
      const body = {
        name: name.trim(), scope: acl.scope, group_by: gb,
        group_attr_key: gak, group_field_scope: gfs,
        visibility_roles: acl.visibility_roles,
        visibility_users_include: acl.visibility_users_include,
        visibility_users_exclude: acl.visibility_users_exclude,
        group_date_mode: gb === 'data' ? dateMode : null,
        group_date_from: (gb === 'data' && dateMode === 'personalizado') ? (dateFrom || null) : null,
        group_date_to: (gb === 'data' && dateMode === 'personalizado') ? (dateTo || null) : null,
        group_date_grain: (gb === 'data' && dateMode === 'personalizado') ? dateGrain : null,
      };
      const url = editing ? `${api.apiBase}/kanban-views/${view.id}` : `${api.apiBase}/kanban-views`;
      const r = await fetch(url, {
        method: editing ? 'PUT' : 'POST',
        headers: { ...api.services.authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const j = await r.json().catch(() => ({}));
      if (!(j && j.ok)) { setErr((j && j.error) || 'Falha ao salvar.'); setSaving(false); return; }
      const vid = (j.data && j.data.id != null) ? j.data.id : (editing ? view.id : null);
      onSaved({ id: vid });
    } catch (_) { setErr('Falha ao salvar.'); setSaving(false); }
  };

  const fieldCls = 'wa-field px-3 py-2 rounded-md text-[13px]';
  const title = editing ? 'Editar visualização' : 'Novo agrupamento';
  return html`
    <div class="fixed inset-0 bg-black/50 z-[75] flex items-center justify-center p-4">
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-lg w-full p-6 max-h-[85vh] overflow-auto">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-base font-semibold text-wa-text">${title}</h2>
          <button onClick=${onCancel} class="text-wa-secondary hover:text-wa-text text-xl leading-none">×</button>
        </div>

        ${canEditMeta ? html`
        <label class="block text-[12px] text-wa-secondary mb-1">Nome</label>
        <input class="${fieldCls} w-full mb-4" type="text" value=${name}
          placeholder="Ex.: Por etapa de venda" onInput=${(e) => setName(e.target.value)} />

        <div class="mb-4">
          <span class="block text-[12px] text-wa-secondary mb-1">Quem pode ver</span>
          <div class="inline-flex rounded-lg border border-wa-border overflow-hidden mb-2">
            ${[['grupos', 'Grupos'], ['usuarios', 'Usuários']].map(([k, lbl]) => html`
              <button key=${k} type="button" onClick=${() => setVisTab(k)}
                class="px-3 py-1 text-[12px] ${visTab === k ? 'bg-wa-teal text-white' : 'bg-wa-panel text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
          </div>
          ${!canTeam ? html`<div class="text-[11px] text-wa-secondary mb-1">Sem permissão para compartilhar — a visualização fica só sua (pessoal).</div>` : null}
          ${visTab === 'grupos' ? html`
            <div class="flex flex-wrap gap-x-4 gap-y-1.5 text-[13px] text-wa-text">
              ${(roles || []).length ? (roles || []).map((r) => html`
                <label key=${r.key} class="inline-flex items-center gap-1.5 ${canTeam ? 'cursor-pointer' : 'opacity-50 cursor-not-allowed'}">
                  <input type="checkbox" disabled=${!canTeam} checked=${visRoles.has(r.key)} onChange=${() => canTeam && toggleRole(r.key)} /> ${r.name || r.key}
                </label>`) : html`<span class="text-[11px] text-wa-secondary">Nenhum grupo disponível.</span>`}
            </div>` : html`
            <div class="max-h-40 overflow-auto rounded-md border border-wa-border p-2 space-y-1">
              ${(users || []).length ? (users || []).map((u) => html`
                <div key=${u.id} class="flex items-center justify-between gap-2 text-[13px]">
                  <span class="text-wa-text truncate" title=${u.name || `Usuário #${u.id}`}>${u.name || `Usuário #${u.id}`}</span>
                  <div class="inline-flex rounded-md border border-wa-border overflow-hidden shrink-0">
                    ${[['padrao', 'Padrão'], ['include', 'Incluir'], ['exclude', 'Excluir']].map(([st, lbl]) => html`
                      <button key=${st} type="button" disabled=${!canTeam} onClick=${() => canTeam && setUserState(u.id, st)}
                        class="px-2 py-0.5 text-[11px] ${canTeam ? '' : 'opacity-50 cursor-not-allowed'} ${userState(u.id) === st ? (st === 'exclude' ? 'bg-red-500 text-white' : (st === 'include' ? 'bg-wa-teal text-white' : 'bg-wa-hover text-wa-text')) : 'bg-wa-panel text-wa-secondary hover:bg-wa-hover'}">${lbl}</button>`)}
                  </div>
                </div>`) : html`<span class="text-[11px] text-wa-secondary">Nenhum usuário.</span>`}
            </div>`}
          <div class="text-[11px] text-wa-secondary mt-1">
            ${aclBody().scope === 'team'
              ? 'Compartilhada: veem os grupos e usuários incluídos (menos os excluídos). Você e admins veem sempre.'
              : 'Sem grupos/usuários selecionados: só você vê esta visualização (pessoal).'}
          </div>
        </div>

        <label class="block text-[12px] text-wa-secondary mb-1">Agrupar por</label>
        <div class="mb-2">
          <${OptionListSelect} grouped=${true} value=${groupSel}
            onChange=${(v) => setGroupSel(v || 'status')}
            searchPlaceholder="Pesquisar campo…"
            options=${[
              { value: 'status', label: 'Status', group: 'Nativos' },
              { value: 'atendente', label: 'Atendente', group: 'Nativos' },
              { value: 'data', label: 'Data', group: 'Nativos' },
              ...protoGroupFields.map((d) => ({
                value: `pfield:protocolo:${d.key}`, label: d.label, group: 'Campos do protocolo' })),
              ...atendGroupFields.map((d) => ({
                value: `pfield:atendimento:${d.key}`, label: d.label, group: 'Campos de resolver atendimento' })),
            ]} />
        </div>
        ${groupSel === 'data' ? html`
          <select class="${fieldCls} w-full mb-2" value=${dateMode} onChange=${(e) => setDateMode(e.target.value)}>
            <option value="faixas">Faixas relativas (Hoje, Ontem, …)</option>
            <option value="dia">Por dia</option>
            <option value="semana">Por semana</option>
            <option value="mes">Por mês</option>
            <option value="personalizado">Período personalizado</option>
          </select>
          ${dateMode === 'personalizado' ? html`
            <div class="mb-4 p-3 rounded-lg bg-wa-panel border border-wa-border">
              <span class="block text-[12px] text-wa-secondary mb-1.5">Período (de/até) e granularidade</span>
              <div class="flex flex-wrap items-center gap-2">
                <input type="date" class="${fieldCls}" value=${dateFrom} onInput=${(e) => setDateFrom(e.target.value)} />
                <span class="text-wa-secondary">→</span>
                <input type="date" class="${fieldCls}" value=${dateTo} onInput=${(e) => setDateTo(e.target.value)} />
                <select class="${fieldCls}" value=${dateGrain} onChange=${(e) => setDateGrain(e.target.value)}>
                  <option value="dia">Por dia</option>
                  <option value="semana">Por semana</option>
                  <option value="mes">Por mês</option>
                </select>
              </div>
            </div>` : html`<div class="mb-2"></div>`}` : html`<div class="mb-4"></div>`}
        ` : html`<div class="text-[13px] text-wa-secondary mb-4">Sem permissão para editar esta visualização. Ajuste os filtros da aba no painel "Configurar filtros da aba", acima da barra de filtros.</div>`}

        ${err ? html`<div class="mb-3 px-3 py-2 rounded-md text-[13px] bg-red-500/15 border border-red-500 text-red-500 font-semibold">${err}</div>` : null}
        <div class="flex justify-end gap-2">
          <button onClick=${onCancel} class="px-4 py-2 rounded-lg bg-wa-panel border border-wa-border text-wa-text hover:bg-wa-hover text-[14px]">Cancelar</button>
          ${canEditMeta ? html`<button onClick=${save} disabled=${saving}
            class="px-4 py-2 rounded-lg bg-wa-teal text-white hover:opacity-90 disabled:opacity-50 text-[14px] font-medium">${saving ? 'Salvando…' : 'Salvar'}</button>` : null}
        </div>
      </div>
    </div>`;
}

export default ProtocolosTab;
