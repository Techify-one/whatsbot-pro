// Tela de configuração (screen config:true) do plugin Protocolos. Quatro abas:
//  - "Protocolo" e "Resolver atendimento": field-builder dos rótulos (fixos + extras).
//  - "Avaliação": os 2 links (título+link) enviados ao FINALIZAR o protocolo
//    (1 normal → WhatsApp, 1 privado → painel), com assignee_id + id_protocol na URL.
//  - "Configurações gerais": toggles de comportamento do plugin.
// Field-builder salva via PUT /field-defs; a aba Avaliação via PUT /protocol-config.
// Remover e Salvar pedem confirmação. Renderizada dentro do modal "Configurar".

import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';
import { authHeaders } from '/static/js/services/api.js';
import { notifyPermissionDenied } from '/static/js/services/notify.js';
import { OptionListSelect } from '/static/js/components/OptionListSelect.js';
import { AttributeValueInput } from '/static/js/components/AttributeValueInput.js';

const html = htm.bind(h);

// fetch + JSON com feedback de 403 (toast "Permissão negada."). O corpo já vem
// unificado do back-end ({ok:false,error}), então a mensagem inline também aparece.
async function reqJson(url, init) {
  const r = await fetch(url, init);
  if (r.status === 403) notifyPermissionDenied();
  return r.json().catch(() => ({}));
}

// Tipos CRIÁVEIS pelo operador. "atendente" NÃO está aqui: virou rótulo fixo/obrigatório,
// invisível nesta tela — aparece só em "Resolver atendimento"/"Finalizar protocolo".
const TYPES = [
  ['text', 'Texto'], ['textarea', 'Área de texto'], ['number', 'Número'], ['date', 'Data'],
  ['select', 'Lista de seleção'], ['checkboxes', 'Caixa de seleção'],
  ['checkbox', 'Caixa (sim/não)'],
];
// Abas: as 2 primeiras são escopos de rótulos; as demais são configs do plugin.
const TABS = [['protocolo', 'Protocolo'], ['atendimento', 'Resolver atendimento'], ['avaliacao', 'Avaliação'], ['geral', 'Configurações gerais']];
const FIELD_TABS = ['protocolo', 'atendimento'];
// Rótulo de origem de cada opção do seletor "não enviar avaliação" (aba Avaliação):
// atributos personalizados do core (contato/conversa) + rótulos da aba "Protocolo".
const SCOPE_LABEL = { contact: 'contato', conversation: 'conversa', protocolo: 'protocolo' };
// Cabeçalho de cada grupo no seletor com busca (OptionListSelect grouped).
const SCOPE_GROUP = { contact: 'Contato', conversation: 'Conversa', protocolo: 'Protocolo' };
// Opções do seletor de atributo: `scope::key` (o par é a identidade) + grupo por escopo.
const attrOptions = (defs) => (defs || []).map((a) => ({
  value: `${a.scope}::${a.key}`, label: a.label, group: SCOPE_GROUP[a.scope] || a.scope,
}));
// Operadores de uma condição da regra "não enviar avaliação". Os 2 últimos dispensam
// o campo de valor (a comparação é sobre o atributo estar preenchido ou não).
const SKIP_OPS = [
  ['eq', 'é igual a'], ['neq', 'é diferente de'],
  ['contains', 'contém'], ['not_contains', 'não contém'],
  ['filled', 'está preenchido'], ['empty', 'está vazio'],
];
const SKIP_OPS_NO_VALUE = ['filled', 'empty'];
// Como as condições da MESMA linha se combinam (entre linhas é sempre OU).
const SKIP_JOINS = [['any', 'Qualquer uma (OU)'], ['all', 'Todas (E)']];
const CONDITION_EMPTY = { op: 'eq', value: '' };
// Regra vinda do servidor no formato antigo (um único `value` = igualdade) → condições.
const ruleConditions = (r) => (Array.isArray(r && r.conditions) && r.conditions.length
  ? r.conditions
  : [{ op: 'eq', value: (r && r.value) || '' }]);
// Direções da regra "ignorar abertura" (qual lado da conversa é analisado).
const SKIP_DIRECTIONS = [
  ['sent', 'Mensagens enviadas (pelo atendente/IA)'],
  ['received', 'Mensagens recebidas (do contato)'],
  ['both', 'Ambas'],
];

function slug(s) {
  return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 48) || 'campo';
}

export default function ProtocolosConfig({ apiBase = '/api/plugins/protocolos', can }) {
  // Esta tela grava via PUT /field-defs, /protocol-config, /general-config e
  // /skip-open-config — todas gateadas por `config` no backend (e o próprio screen
  // declara `requires: config`). Gatear aqui por `edit` deixaria os campos editáveis
  // para quem tomaria 403 ao salvar.
  const canEdit = !can || can('config');
  const [tab, setTab] = useState('protocolo'); // 'protocolo' | 'atendimento' | 'avaliacao'
  const [defs, setDefs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState('');
  const [confirmBox, setConfirmBox] = useState(null); // { message, onYes }
  const [proto, setProto] = useState(null);           // config de avaliação ao finalizar
  const [protoMsg, setProtoMsg] = useState('');
  const [general, setGeneral] = useState(null);       // configurações gerais do plugin
  const [generalMsg, setGeneralMsg] = useState('');
  const [skip, setSkip] = useState(null);             // regra "ignorar abertura por regex"
  const [attrDefs, setAttrDefs] = useState([]);       // atributos (contato+conversa) p/ o skip da avaliação
  const [protoFieldDefs, setProtoFieldDefs] = useState([]);  // rótulos da aba "Protocolo" p/ o mesmo skip

  const load = useCallback(async (sc) => {
    setLoading(true); setMsg('');
    try {
      const d = await reqJson(`${apiBase}/field-defs?scope=${sc}`, { headers: authHeaders() });
      // O rótulo fixo "atendente" não aparece na tela de Configurações (só nos formulários).
      const all = (d && d.ok && d.data && d.data.defs) || [];
      setDefs(all.filter((x) => x.type !== 'atendente'));
    } finally { setLoading(false); }
  }, [apiBase]);

  // Carrega os rótulos só nas abas de campos (a aba Avaliação não usa field-defs).
  useEffect(() => { if (FIELD_TABS.includes(tab)) load(tab); }, [tab, load]);

  const PROTO_EMPTY = { enabled: false, normal: { title: '', link: '' }, privado: { title: '', link: '' }, skip_attrs: [] };
  const RELINK_ATTR_EMPTY = { enabled: false, scope: 'contact', key: '', values: { previous: '', new: '', block: '' }, consume: true };
  const GENERAL_EMPTY = { auto_assign_conversation_on_close: true, resolve_keep_assignee: false, reactivate_ai_on_close: true, relink_prompt_enabled: true, relink_window_minutes: 30, relink_attr: RELINK_ATTR_EMPTY };
  const SKIP_EMPTY = { enabled: false, regex: '', direction: 'sent' };
  const loadProto = useCallback(async () => {
    try {
      const d = await reqJson(`${apiBase}/protocol-config`, { headers: authHeaders() });
      setProto((d && d.ok && d.data) || PROTO_EMPTY);
    } catch (_) { setProto(PROTO_EMPTY); }
  }, [apiBase]);
  useEffect(() => { loadProto(); }, [loadProto]);

  const loadGeneral = useCallback(async () => {
    try {
      const d = await reqJson(`${apiBase}/general-config`, { headers: authHeaders() });
      setGeneral((d && d.ok && d.data) || GENERAL_EMPTY);
    } catch (_) { setGeneral(GENERAL_EMPTY); }
  }, [apiBase]);
  useEffect(() => { loadGeneral(); }, [loadGeneral]);

  const loadSkip = useCallback(async () => {
    try {
      const d = await reqJson(`${apiBase}/skip-open-config`, { headers: authHeaders() });
      setSkip((d && d.ok && d.data) || SKIP_EMPTY);
    } catch (_) { setSkip(SKIP_EMPTY); }
  }, [apiBase]);
  useEffect(() => { loadSkip(); }, [loadSkip]);

  // Definições de atributos personalizados (contato + conversa) p/ o seletor da regra
  // "não enviar avaliação" (aba Avaliação). O escopo rotula cada opção p/ o operador.
  const loadAttrDefs = useCallback(async () => {
    try {
      const [cc, cv] = await Promise.all([
        reqJson('/api/custom-attributes?applies_to=contact', { headers: authHeaders() }),
        reqJson('/api/custom-attributes?applies_to=conversation', { headers: authHeaders() }),
      ]);
      const map = (rows, scope) => ((rows && rows.ok && rows.data) || []).map((d) => ({
        key: d.attribute_key, label: d.display_name || d.attribute_key, scope, type: d.type,
        options: Array.isArray(d.options) ? d.options : [],
      }));
      setAttrDefs([...map(cc, 'contact'), ...map(cv, 'conversation')]);
    } catch (_) { setAttrDefs([]); }
  }, []);
  useEffect(() => { loadAttrDefs(); }, [loadAttrDefs]);

  // Rótulos da aba "Protocolo" (sistema de campos PRÓPRIO do plugin) — também
  // selecionáveis na regra "não enviar avaliação". Carregados à parte dos
  // `defs` (que seguem a aba aberta) para a aba Avaliação não depender dela.
  // "atendente" fica de fora: é campo fixo de pessoa, não comparável por valor.
  const loadProtoFieldDefs = useCallback(async () => {
    try {
      const d = await reqJson(`${apiBase}/field-defs?scope=protocolo`, { headers: authHeaders() });
      const all = (d && d.ok && d.data && d.data.defs) || [];
      setProtoFieldDefs(all.filter((x) => x.type !== 'atendente').map((x) => ({
        key: x.key, label: x.label || x.key, scope: 'protocolo', type: x.type,
        options: Array.isArray(x.options) ? x.options : [],
      })));
    } catch (_) { setProtoFieldDefs([]); }
  }, [apiBase]);
  useEffect(() => { loadProtoFieldDefs(); }, [loadProtoFieldDefs]);

  // Opções do seletor da regra de skip: atributos do core + rótulos do protocolo.
  const skipDefs = [...attrDefs, ...protoFieldDefs];

  function update(i, patch) {
    setDefs((list) => list.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  }
  function addField() {
    setDefs((list) => [...list, { key: '', label: '', type: 'text', options: [], required: false, multiple: false, regex_pattern: '', regex_cue: '', fixed: false }]);
  }
  function remove(i) { setDefs((list) => list.filter((_, j) => j !== i)); }

  async function save() {
    setMsg('');
    const payload = defs.map((d) => ({
      ...d,
      key: d.key && d.key.trim() ? d.key.trim() : slug(d.label),
      // Opções por linha OU por vírgula (rótulos digitados numa linha só viram opções separadas),
      // com trim + sem vazios + sem duplicar. Espelha o splitOptionList do filtro (protocolos_tab.js).
      // Consequência: uma opção não pode conter vírgula — ela seria dividida.
      options: [...new Set((d.options || [])
        .flatMap((s) => String(s).split(/[\n,]/)).map((s) => s.trim()).filter(Boolean))],
    }));
    const d = await reqJson(`${apiBase}/field-defs`, {
      method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: tab, defs: payload }),
    });
    if (d && d.ok) { setDefs((d.data.defs || []).filter((x) => x.type !== 'atendente')); setMsg('Campos salvos.'); }
    else setMsg((d && d.error) || 'Falha ao salvar.');
  }

  async function saveProto() {
    setProtoMsg('');
    const d = await reqJson(`${apiBase}/protocol-config`, {
      method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(proto),
    });
    if (d && d.ok) { setProto(d.data); setProtoMsg('Configuração salva.'); }
    else setProtoMsg((d && d.error) || 'Falha ao salvar.');
  }

  // Um único "Salvar configurações" para a aba inteira: grava as configs gerais
  // (/general-config) E a regra de ignorar abertura (/skip-open-config) de uma vez.
  async function saveGeralTab() {
    setGeneralMsg('');
    const put = (path, body) => reqJson(`${apiBase}/${path}`, {
      method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const [g, s] = await Promise.all([
      put('general-config', general || GENERAL_EMPTY),
      put('skip-open-config', skip || SKIP_EMPTY),
    ]);
    if (g && g.ok) setGeneral(g.data);
    if (s && s.ok) setSkip(s.data);
    const ok = g && g.ok && s && s.ok;
    setGeneralMsg(ok ? 'Configurações salvas.' : ((g && g.error) || (s && s.error) || 'Falha ao salvar.'));
  }

  // Regras {key, scope, join, conditions[]} de "não enviar avaliação" — vivem em
  // proto.skip_attrs (salvas junto no PUT /protocol-config pelo botão "Salvar avaliação").
  function addSkipAttr() {
    setProto((p) => ({ ...(p || PROTO_EMPTY),
      skip_attrs: [...(((p || {}).skip_attrs) || []),
                   { key: '', scope: 'contact', join: 'any', conditions: [{ ...CONDITION_EMPTY }] }] }));
  }
  function updateSkipAttr(i, patch) {
    setProto((p) => ({ ...p,
      skip_attrs: (((p || {}).skip_attrs) || []).map((r, j) => (j === i ? { ...r, ...patch } : r)) }));
  }
  function removeSkipAttr(i) {
    setProto((p) => ({ ...p, skip_attrs: (((p || {}).skip_attrs) || []).filter((_, j) => j !== i) }));
  }
  // Condições DENTRO de uma linha (mesmo atributo, combinadas por `join`).
  function updateSkipCond(i, ci, patch) {
    setProto((p) => ({ ...p, skip_attrs: (((p || {}).skip_attrs) || []).map((r, j) => (j === i
      ? { ...r, conditions: ruleConditions(r).map((c, k) => (k === ci ? { ...c, ...patch } : c)) }
      : r)) }));
  }
  function addSkipCond(i) {
    setProto((p) => ({ ...p, skip_attrs: (((p || {}).skip_attrs) || []).map((r, j) => (j === i
      ? { ...r, join: r.join || 'any', conditions: [...ruleConditions(r), { ...CONDITION_EMPTY }] }
      : r)) }));
  }
  function removeSkipCond(i, ci) {
    setProto((p) => ({ ...p, skip_attrs: (((p || {}).skip_attrs) || []).map((r, j) => (j === i
      ? { ...r, conditions: ruleConditions(r).filter((_, k) => k !== ci) }
      : r)) }));
  }

  // Regra "decidir a continuidade pelo atributo personalizado" — vive em
  // general.relink_attr (salva junto no PUT /general-config pelo botão da aba geral).
  function updateRelinkAttr(patch) {
    setGeneral((g) => {
      const base = g || GENERAL_EMPTY;
      return { ...base, relink_attr: { ...RELINK_ATTR_EMPTY, ...(base.relink_attr || {}), ...patch } };
    });
  }
  function updateRelinkAttrValue(kind, value) {
    setGeneral((g) => {
      const base = g || GENERAL_EMPTY;
      const ra = { ...RELINK_ATTR_EMPTY, ...(base.relink_attr || {}) };
      return { ...base, relink_attr: { ...ra, values: { ...(ra.values || {}), [kind]: value } } };
    });
  }

  function askRemove(i, d) {
    setConfirmBox({
      message: `Remover o rótulo "${d.label || d.key || 'sem nome'}"? Ele some do menu de `
        + 'criação/edição e do histórico. Os dados já gravados continuam recuperáveis apenas '
        + 'pelo banco — não há como recuperá-los pela interface.',
      onYes: () => remove(i),
    });
  }
  function askSave() {
    setConfirmBox({ message: 'Salvar as alterações nos rótulos deste escopo?', onYes: save });
  }

  // ── Aba de rótulos (field-builder) ──────────────────────────────────────────
  const fieldBuilder = html`
    <div class="space-y-4">
      <p class="text-[13px] text-wa-secondary">
        Crie rótulos (texto, seleção, etc.); <b>todos são editáveis e removíveis</b>, inclusive
        <b>Observações</b> (ID, Início e Fim são preenchidos automaticamente, não são rótulos).
        O <b>Atendente</b> é um campo fixo e obrigatório que aparece automaticamente em
        "Resolver atendimento" e "Finalizar protocolo" — não é
        configurável aqui. Os do <b>Protocolo</b> e os de <b>Resolver atendimento</b> são
        armazenados separadamente. Um campo <b>Obrigatório</b> deve estar sempre preenchido para
        fechar/resolver (caixas de seleção são exceção). Apagar um rótulo o some do menu e do
        histórico — o dado fica recuperável apenas pelo banco.
      </p>
      ${loading ? html`<div class="text-[13px] text-wa-secondary">Carregando…</div>` : html`
        <div class="space-y-3">
          ${defs.map((d, i) => {
            const isFixed = !!d.fixed;
            const locked = isFixed || !canEdit;
            return html`
            <div key=${d.id || i} class="p-3 rounded-lg border ${isFixed ? 'border-wa-border bg-wa-hover/40' : 'border-wa-border bg-wa-panel'} space-y-2">
              <div class="flex flex-wrap gap-2 items-end">
                <div class="flex-1 min-w-[140px]">
                  <label class="block text-[12px] text-wa-secondary mb-1">
                    Rótulo ${isFixed ? html`<span class="ml-1 px-1.5 py-0.5 rounded text-[10px] bg-wa-teal/15 text-wa-teal align-middle">Fixo</span>` : null}
                  </label>
                  <input class="wa-field w-full px-2 py-1.5 rounded-md text-[13px]" type="text"
                    value=${d.label} disabled=${locked}
                    onInput=${(e) => update(i, { label: e.target.value })} />
                </div>
                <div class="w-[150px]">
                  <label class="block text-[12px] text-wa-secondary mb-1">Tipo</label>
                  <select class="wa-field w-full px-2 py-1.5 rounded-md text-[13px]" value=${d.type}
                    disabled=${locked} onChange=${(e) => update(i, { type: e.target.value })}>
                    ${TYPES.map(([t, lbl]) => html`<option key=${t} value=${t}>${lbl}</option>`)}
                  </select>
                </div>
                <label class="flex items-center gap-1.5 text-[13px] text-wa-text pb-1.5">
                  <input type="checkbox" checked=${!!d.required} disabled=${!canEdit}
                    onChange=${(e) => update(i, { required: e.target.checked })} /> Obrigatório
                </label>
                ${(!isFixed && canEdit) ? html`<button onClick=${() => askRemove(i, d)}
                  class="text-red-500 hover:text-red-600 text-[13px] pb-1.5">Remover</button>` : null}
              </div>
              ${(d.type === 'select' || d.type === 'radio' || d.type === 'checkboxes') ? html`
                <div>
                  <label class="block text-[12px] text-wa-secondary mb-1">Opções (uma por linha ou separadas por vírgula)</label>
                  <textarea class="wa-field w-full px-2 py-1.5 rounded-md text-[13px] min-h-[64px]"
                    disabled=${locked}
                    value=${(d.options || []).join('\n')}
                    onInput=${(e) => update(i, { options: e.target.value.split('\n') })} />
                </div>` : null}
              ${(d.type === 'checkboxes' || d.type === 'select') ? html`
                <label class="flex items-center gap-1.5 text-[13px] text-wa-text">
                  <input type="checkbox" checked=${!!d.multiple} disabled=${locked}
                    onChange=${(e) => update(i, { multiple: e.target.checked })} /> Permitir marcar várias opções
                </label>` : null}
              ${(!isFixed && (d.type === 'text' || d.type === 'textarea' || d.type === 'number')) ? html`
                <div class="grid grid-cols-2 gap-2">
                  <div>
                    <label class="block text-[12px] text-wa-secondary mb-1">Regex (opcional)</label>
                    <input class="wa-field w-full px-2 py-1.5 rounded-md text-[13px] font-mono" type="text"
                      placeholder="^\\d{11}$" disabled=${locked}
                      value=${d.regex_pattern || ''}
                      onInput=${(e) => update(i, { regex_pattern: e.target.value })} />
                  </div>
                  <div>
                    <label class="block text-[12px] text-wa-secondary mb-1">Dica do formato</label>
                    <input class="wa-field w-full px-2 py-1.5 rounded-md text-[13px]" type="text"
                      placeholder="11 dígitos" disabled=${locked}
                      value=${d.regex_cue || ''}
                      onInput=${(e) => update(i, { regex_cue: e.target.value })} />
                  </div>
                </div>` : null}
            </div>`;
          })}
        </div>
        ${canEdit ? html`
          <div class="flex items-center gap-3">
            <button onClick=${addField} class="px-3 py-1.5 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover">+ Adicionar campo</button>
            <button onClick=${askSave} class="px-4 py-1.5 rounded-md text-[13px] bg-wa-teal text-white">Salvar campos</button>
            ${msg ? html`<span class="text-[12px] text-wa-secondary">${msg}</span>` : null}
          </div>` : html`<div class="text-[12px] text-wa-secondary">Sem permissão para editar.</div>`}
      `}
    </div>`;

  // ── Aba de avaliação (links de protocolo enviados ao finalizar) ─────────────
  const avaliacao = html`
    <div class="space-y-3">
      <p class="text-[12px] text-wa-secondary">
        Ao <b>finalizar</b> um protocolo, envia 2 mensagens ao contato: a <b>normal</b> vai ao
        WhatsApp e a <b>privada</b> fica só no painel. Em ambos os links são adicionados, como
        parâmetros de URL, o id do atendente (<b>assignee_id</b>) e um id de protocolo único
        gerado no envio (<b>id_protocol</b>).
      </p>
      ${proto === null ? html`<div class="text-[12px] text-wa-secondary">Carregando…</div>` : html`
        <label class="flex items-center gap-2 text-[13px] text-wa-text">
          <input type="checkbox" checked=${!!proto.enabled} disabled=${!canEdit}
            onChange=${(e) => setProto((p) => ({ ...p, enabled: e.target.checked }))} />
          Ativar envio ao finalizar
        </label>
        ${[['normal', 'Mensagem normal (WhatsApp)'], ['privado', 'Mensagem privada (painel)']].map(([k, lbl]) => html`
          <div key=${k} class="p-3 rounded-lg border border-wa-border bg-wa-panel space-y-2">
            <div class="text-[12px] font-semibold text-wa-text">${lbl}</div>
            <div>
              <label class="block text-[12px] text-wa-secondary mb-1">Título</label>
              <input class="wa-field w-full px-2 py-1.5 rounded-md text-[13px]" type="text"
                placeholder=${k === 'normal' ? 'AVALIE NOSSO PROTOCOLO' : 'AVALIAÇÃO INTERNA DE CLIENTE'}
                value=${proto[k].title} disabled=${!canEdit}
                onInput=${(e) => setProto((p) => ({ ...p, [k]: { ...p[k], title: e.target.value } }))} />
            </div>
            <div>
              <label class="block text-[12px] text-wa-secondary mb-1">Link</label>
              <input class="wa-field w-full px-2 py-1.5 rounded-md text-[13px]" type="text"
                placeholder="https://..."
                value=${proto[k].link} disabled=${!canEdit}
                onInput=${(e) => setProto((p) => ({ ...p, [k]: { ...p[k], link: e.target.value } }))} />
            </div>
          </div>`)}
        <div class="p-3 rounded-lg border border-wa-border bg-wa-panel space-y-2">
          <div class="text-[12px] font-semibold text-wa-text">Não enviar avaliação quando o contato/conversa/protocolo tiver o atributo:</div>
          <p class="text-[12px] text-wa-secondary">
            Cada linha é um atributo — do contato, da conversa ou um <b>rótulo da aba Protocolo</b> —
            com uma ou mais condições sobre ele. Várias condições na mesma linha se combinam por
            <b>Qualquer uma (OU)</b> ou <b>Todas (E)</b>; entre linhas diferentes é sempre <b>OU</b>.
            Bastando uma linha casar, ao finalizar o protocolo <b>nem</b> a mensagem normal <b>nem</b>
            a privada são enviadas.
          </p>
          ${((proto.skip_attrs) || []).map((r, i) => {
            const def = skipDefs.find((a) => a.key === r.key && a.scope === r.scope);
            const opts = (def && def.options) || [];
            const conds = ruleConditions(r);
            // O escopo do atributo escolhido vira parte do rótulo — "Atributo (conversa)".
            const scopeName = def ? (SCOPE_LABEL[def.scope] || def.scope) : '';
            return html`
            <div key=${i} class="p-2 rounded-md border border-wa-border space-y-2">
              <div class="flex flex-wrap gap-2 items-end">
                <div class="flex-1 min-w-[160px]">
                  <label class="block text-[12px] text-wa-secondary mb-1">
                    ${scopeName ? `Atributo (${scopeName})` : 'Atributo'}
                  </label>
                  ${canEdit ? html`
                    <${OptionListSelect} options=${attrOptions(skipDefs)} grouped=${true} float=${true}
                      value=${r.key ? `${r.scope}::${r.key}` : ''}
                      placeholder="— selecione —" searchPlaceholder="Pesquisar atributo…"
                      onChange=${(v) => { const ix = String(v || '').indexOf('::');
                        // Trocar de atributo zera as condições: as opções do valor são
                        // as do campo antigo e não valem mais para o novo.
                        updateSkipAttr(i, ix < 0
                          ? { scope: 'contact', key: '', value: '', conditions: [{ ...CONDITION_EMPTY }] }
                          : { scope: v.slice(0, ix), key: v.slice(ix + 2), value: '',
                              conditions: [{ ...CONDITION_EMPTY }] }); }} />`
                  : html`<div class="wa-field w-full px-2 py-1.5 rounded-md text-[13px] opacity-60">
                      ${def ? `${def.label} (${scopeName})` : '— selecione —'}
                    </div>`}
                </div>
                ${conds.length > 1 ? html`
                  <div class="min-w-[150px]">
                    <label class="block text-[12px] text-wa-secondary mb-1">Combinação</label>
                    <select class="wa-field w-full px-2 py-1.5 rounded-md text-[13px]" disabled=${!canEdit}
                      value=${r.join || 'any'}
                      onChange=${(e) => updateSkipAttr(i, { join: e.target.value })}>
                      ${SKIP_JOINS.map(([v, lbl]) => html`<option key=${v} value=${v}>${lbl}</option>`)}
                    </select>
                  </div>` : null}
                ${canEdit ? html`<button onClick=${() => removeSkipAttr(i)}
                  class="text-red-500 hover:text-red-600 text-[13px] pb-1.5">Remover</button>` : null}
              </div>
              ${conds.map((c, ci) => {
                const noValue = SKIP_OPS_NO_VALUE.includes(c.op || 'eq');
                return html`
                <div key=${ci} class="flex flex-wrap gap-2 items-end">
                  <div class="min-w-[150px]">
                    <label class="block text-[12px] text-wa-secondary mb-1">
                      ${ci === 0 ? 'Condição' : ((r.join || 'any') === 'all' ? 'E também' : 'Ou então')}
                    </label>
                    <select class="wa-field w-full px-2 py-1.5 rounded-md text-[13px]" disabled=${!canEdit}
                      value=${c.op || 'eq'}
                      onChange=${(e) => updateSkipCond(i, ci, { op: e.target.value })}>
                      ${SKIP_OPS.map(([v, lbl]) => html`<option key=${v} value=${v}>${lbl}</option>`)}
                    </select>
                  </div>
                  ${noValue ? null : html`
                    <div class="flex-1 min-w-[140px]">
                      <label class="block text-[12px] text-wa-secondary mb-1">Valor</label>
                      <${AttributeValueInput} type=${def && def.type} options=${opts}
                        value=${c.value} disabled=${!canEdit}
                        onChange=${(v) => updateSkipCond(i, ci, { value: v })} />
                    </div>`}
                  ${canEdit && conds.length > 1 ? html`<button onClick=${() => removeSkipCond(i, ci)}
                    class="text-red-500 hover:text-red-600 text-[13px] pb-1.5">Remover condição</button>` : null}
                </div>`;
              })}
              ${canEdit ? html`<button onClick=${() => addSkipCond(i)}
                class="px-2 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover">
                + Adicionar condição
              </button>` : null}
            </div>`;
          })}
          ${canEdit ? html`<button onClick=${addSkipAttr}
            class="px-3 py-1.5 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover">+ Adicionar atributo</button>` : null}
        </div>
        ${canEdit ? html`
          <div class="flex items-center gap-3">
            <button onClick=${() => setConfirmBox({ message: 'Salvar a configuração de avaliação ao finalizar?', onYes: saveProto })}
              class="px-4 py-1.5 rounded-md text-[13px] bg-wa-teal text-white">Salvar avaliação</button>
            ${protoMsg ? html`<span class="text-[12px] text-wa-secondary">${protoMsg}</span>` : null}
          </div>` : null}
      `}
    </div>`;

  // ── Aba de configurações gerais do plugin (inclui a ÁREA "Ignorar abertura") ─
  // Um único botão "Salvar configurações" no fim salva TODAS as opções desta aba.
  const geral = html`
    <div class="space-y-3">
      ${general === null ? html`<div class="text-[12px] text-wa-secondary">Carregando…</div>` : html`
        <div class="p-3 rounded-lg border border-wa-border bg-wa-panel space-y-2">
          <label class="flex items-start gap-2 text-[13px] text-wa-text">
            <input class="mt-0.5" type="checkbox"
              checked=${general.auto_assign_conversation_on_close !== false}
              disabled=${!canEdit}
              onChange=${(e) => setGeneral((g) => ({ ...(g || GENERAL_EMPTY), auto_assign_conversation_on_close: e.target.checked }))} />
            <span>
              <span class="font-medium">Atribuir atendente à conversa ao finalizar protocolo</span>
              <span class="block text-[12px] text-wa-secondary mt-0.5">
                Quando ativo, o atendente salvo no protocolo também aparece como atendente da conversa.
              </span>
            </span>
          </label>
          <label class="flex items-start gap-2 text-[13px] text-wa-text">
            <input class="mt-0.5" type="checkbox"
              checked=${general.resolve_keep_assignee === true}
              disabled=${!canEdit}
              onChange=${(e) => setGeneral((g) => ({ ...(g || GENERAL_EMPTY), resolve_keep_assignee: e.target.checked }))} />
            <span>
              <span class="font-medium">Manter o atendente ao resolver a conversa</span>
              <span class="block text-[12px] text-wa-secondary mt-0.5">
                Por padrão o WhatsBot desatribui o atendente ao resolver. Com esta opção ativa a
                conversa resolvida continua vinculada a quem atendeu.
              </span>
            </span>
          </label>
          <label class="flex items-start gap-2 text-[13px] text-wa-text">
            <input class="mt-0.5" type="checkbox"
              checked=${general.reactivate_ai_on_close !== false}
              disabled=${!canEdit}
              onChange=${(e) => setGeneral((g) => ({ ...(g || GENERAL_EMPTY), reactivate_ai_on_close: e.target.checked }))} />
            <span>
              <span class="font-medium">Religar a IA ao finalizar o protocolo</span>
              <span class="block text-[12px] text-wa-secondary mt-0.5">
                Ao finalizar, se o interruptor global da IA e a IA do canal estiverem ligados, a IA
                é religada na conversa na hora (o card "SISTEMA reativou a IA."). Desligue se outro
                plugin (ex.: Vendas IA) controla quando a IA volta após o fechamento.
              </span>
            </span>
          </label>
        </div>
      `}

      ${general === null ? null : html`
        <div class="p-3 rounded-lg border border-wa-border bg-wa-panel space-y-2">
          <label class="flex items-start gap-2 text-[13px] text-wa-text">
            <input class="mt-0.5" type="checkbox"
              checked=${general.relink_prompt_enabled !== false}
              disabled=${!canEdit}
              onChange=${(e) => setGeneral((g) => ({ ...(g || GENERAL_EMPTY), relink_prompt_enabled: e.target.checked }))} />
            <span>
              <span class="font-medium">Perguntar ao reabrir logo após fechar</span>
              <span class="block text-[12px] text-wa-secondary mt-0.5">
                Ao abrir uma conversa cujo contato finalizou um protocolo há pouco, mostra um popup
                perguntando se o atendimento faz parte do protocolo anterior ou é um novo.
              </span>
            </span>
          </label>
          <div class="flex items-center gap-2 pl-6">
            <label class="text-[12px] text-wa-secondary">Janela (minutos)</label>
            <input class="wa-field w-24 px-2 py-1.5 rounded-md text-[13px]" type="number" min="1" max="1440"
              value=${general.relink_window_minutes == null ? 30 : general.relink_window_minutes}
              disabled=${!canEdit || general.relink_prompt_enabled === false}
              onInput=${(e) => setGeneral((g) => ({ ...(g || GENERAL_EMPTY), relink_window_minutes: e.target.value === '' ? '' : Number(e.target.value) }))} />
          </div>
        </div>
      `}

      ${general === null ? null : (() => {
        const ra = { ...RELINK_ATTR_EMPTY, ...(general.relink_attr || {}) };
        const def = attrDefs.find((a) => a.key === ra.key && a.scope === ra.scope);
        const opts = (def && def.options) || [];
        const valueField = (kind, label, hint) => html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">${label}</label>
            <${AttributeValueInput} type=${def && def.type} options=${opts}
              value=${(ra.values || {})[kind] || ''} disabled=${!canEdit}
              placeholder="— não usar —"
              onChange=${(v) => updateRelinkAttrValue(kind, v)} />
            <span class="block text-[11px] text-wa-secondary mt-0.5">${hint}</span>
          </div>`;
        return html`
        <div class="p-3 rounded-lg border border-wa-border bg-wa-panel space-y-2">
          <label class="flex items-start gap-2 text-[13px] text-wa-text">
            <input class="mt-0.5" type="checkbox" checked=${!!ra.enabled} disabled=${!canEdit}
              onChange=${(e) => updateRelinkAttr({ enabled: e.target.checked })} />
            <span>
              <span class="font-medium">Decidir a continuidade por um atributo personalizado</span>
              <span class="block text-[12px] text-wa-secondary mt-0.5">
                A escolha do atendente no pop-up <b>grava</b> o valor abaixo no atributo, e um valor
                já preenchido <b>decide sozinho</b> o próximo re-engajamento (o pop-up não aparece).
                Precedência: <b>não abrir</b> › <b>faz parte do anterior</b> › <b>novo protocolo</b>.
                Atributo vazio ⇒ volta a perguntar.
              </span>
            </span>
          </label>
          <div class="pl-6 space-y-2">
            <div>
              <label class="block text-[12px] text-wa-secondary mb-1">Atributo</label>
              ${canEdit ? html`
                <${OptionListSelect} options=${attrOptions(attrDefs)} grouped=${true} float=${true}
                  value=${ra.key ? `${ra.scope}::${ra.key}` : ''}
                  placeholder="— selecione —" searchPlaceholder="Pesquisar atributo…"
                  onChange=${(v) => { const ix = String(v || '').indexOf('::');
                    updateRelinkAttr(ix < 0
                      ? { scope: 'contact', key: '', values: { previous: '', new: '', block: '' } }
                      : { scope: v.slice(0, ix) || 'contact', key: v.slice(ix + 2),
                        values: { previous: '', new: '', block: '' } }); }} />`
              : html`<div class="wa-field w-full px-2 py-1.5 rounded-md text-[13px] opacity-60">
                  ${def ? `${def.label} (${SCOPE_LABEL[def.scope] || def.scope})` : '— selecione —'}
                </div>`}
            </div>
            ${valueField('previous', 'Valor = faz parte do protocolo anterior',
              'Vincula o atendimento ao protocolo anterior (reabre o anterior).')}
            ${valueField('new', 'Valor = é um novo protocolo',
              'Abre um protocolo novo imediatamente, sem esperar a decisão.')}
            ${valueField('block', 'Valor = não abrir protocolo',
              'Não abre nem vincula, e a conversa não é reaberta. Permanece até o atributo mudar.')}
            <label class="flex items-start gap-2 text-[13px] text-wa-text">
              <input class="mt-0.5" type="checkbox" checked=${ra.consume !== false} disabled=${!canEdit}
                onChange=${(e) => updateRelinkAttr({ consume: e.target.checked })} />
              <span>
                <span class="font-medium">Limpar o valor após aplicar</span>
                <span class="block text-[12px] text-wa-secondary mt-0.5">
                  A decisão vale para um re-engajamento e o valor é apagado — o ciclo seguinte volta
                  a perguntar. Não se aplica ao valor de <b>não abrir protocolo</b>, que permanece.
                </span>
              </span>
            </label>
          </div>
        </div>`;
      })()}

      <div class="p-3 rounded-lg border border-wa-border bg-wa-panel space-y-2">
        <div class="text-[13px] font-semibold text-wa-text">Ignorar abertura por regex</div>
        <p class="text-[12px] text-wa-secondary">
          Quando o texto de uma mensagem casar com a <b>regex</b>, o <b>protocolo não é aberto</b> e a
          conversa é <b>mantida fechada</b> (não reabre) — a mensagem continua salva e visível no painel.
          Nas <b>enviadas</b> (pelo atendente) ela vai ao WhatsApp normalmente; nas <b>recebidas</b>
          (do contato) a <b>IA não responde</b> (senão a resposta reabriria a conversa e abriria protocolo).
          Use a <b>direção</b> para escolher qual lado analisar.
        </p>
        ${skip === null ? html`<div class="text-[12px] text-wa-secondary">Carregando…</div>` : html`
          <label class="flex items-center gap-2 text-[13px] text-wa-text">
            <input type="checkbox" checked=${!!skip.enabled} disabled=${!canEdit}
              onChange=${(e) => setSkip((s) => ({ ...(s || SKIP_EMPTY), enabled: e.target.checked }))} />
            Ativar
          </label>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Regex</label>
            <input class="wa-field w-full px-2 py-1.5 rounded-md text-[13px] font-mono" type="text"
              placeholder="ex.: PROT-\\d" value=${skip.regex || ''} disabled=${!canEdit}
              onInput=${(e) => setSkip((s) => ({ ...(s || SKIP_EMPTY), regex: e.target.value }))} />
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Direção</label>
            <select class="wa-field w-full px-2 py-1.5 rounded-md text-[13px]" disabled=${!canEdit}
              value=${skip.direction || 'sent'}
              onChange=${(e) => setSkip((s) => ({ ...(s || SKIP_EMPTY), direction: e.target.value }))}>
              ${SKIP_DIRECTIONS.map(([v, lbl]) => html`<option key=${v} value=${v}>${lbl}</option>`)}
            </select>
          </div>
        `}
      </div>

      ${canEdit ? html`
        <div class="flex items-center gap-3">
          <button onClick=${() => setConfirmBox({ message: 'Salvar as configurações gerais do plugin (incluindo a regra de ignorar abertura)?', onYes: saveGeralTab })}
            class="px-4 py-1.5 rounded-md text-[13px] bg-wa-teal text-white">Salvar configurações</button>
          ${generalMsg ? html`<span class="text-[12px] text-wa-secondary">${generalMsg}</span>` : null}
        </div>` : null}
    </div>`;

  return html`
    <div class="space-y-4">
      <div class="inline-flex rounded-lg border border-wa-border overflow-hidden">
        ${TABS.map(([k, lbl]) => html`
          <button key=${k} onClick=${() => setTab(k)}
            class="px-3 py-1.5 text-[13px] ${tab === k ? 'bg-wa-teal text-white' : 'bg-wa-panel text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
      </div>

      ${tab === 'avaliacao' ? avaliacao : (tab === 'geral' ? geral : fieldBuilder)}

      ${confirmBox ? html`
        <div class="fixed inset-0 bg-black/50 z-[90] flex items-center justify-center p-4"
          onClick=${(e) => { if (e.target === e.currentTarget) setConfirmBox(null); }}>
          <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-sm w-full p-6">
            <h2 class="text-base font-semibold text-wa-text mb-2">Confirmar</h2>
            <p class="text-[13px] text-wa-secondary mb-5">${confirmBox.message}</p>
            <div class="flex gap-2">
              <button onClick=${() => { const f = confirmBox.onYes; setConfirmBox(null); if (f) f(); }}
                class="flex-1 py-2 px-4 bg-wa-teal text-white rounded-lg">Confirmar</button>
              <button onClick=${() => setConfirmBox(null)}
                class="flex-1 py-2 px-4 bg-wa-panel hover:bg-wa-hover text-wa-text rounded-lg">Cancelar</button>
            </div>
          </div>
        </div>` : null}
    </div>`;
}
