// Cofre de credenciais das tools de IA (plano 177) — seção da aba Tools.
//
// A LISTA nunca traz o valor de um campo secreto: o servidor devolve `{key, secret,
// has_value}` e a tela mostra "definido". Para ver o valor há o OLHO em cada campo
// secreto já salvo: busca SÓ aquele campo, na hora do clique (a rota audita quem leu)
// e volta a ocultar sozinho depois de REVEAL_SECONDS.
// Campo secreto vai em <input type="password" autocomplete="new-password">: o
// `off` é IGNORADO pelo Chrome em campo de senha, e o gerenciador de senhas
// injetaria a senha do painel (CLAUDE.md, plano 104).

import { h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import {
  listCredentials,
  saveCredential,
  deleteCredential,
  revealCredentialField,
  setCredentialTools,
} from '../../services/api.js';
import {
  addField,
  applyReveal,
  credentialPayload,
  eyeAction,
  fieldStatus,
  hideField,
  initialCredentialForm,
  removeField,
  updateField,
  validateCredentialForm,
} from '../../services/credentialFields.js';

const html = htm.bind(h);

// Quanto tempo um valor revelado fica na tela antes de voltar a ser ocultado.
const REVEAL_SECONDS = 30;

// Ícone de olho (aberto = mostrar; riscado = ocultar). SVG inline: sem dependência.
function EyeIcon({ off }) {
  return html`
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
      <circle cx="12" cy="12" r="3" />
      ${off ? html`<line x1="3" y1="3" x2="21" y2="21" />` : null}
    </svg>
  `;
}

function fmtDate(epoch) {
  if (epoch == null) return '—';
  const d = new Date(epoch * 1000);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function CredentialForm({ editing, codeTools, onSave, onCancel, busy, serverError }) {
  const isNew = !editing;
  const [form, setForm] = useState(() => initialCredentialForm(editing));
  const [tools, setTools] = useState(() => new Set((editing && editing.tools) || []));
  const [touched, setTouched] = useState(false);
  const [eyeBusy, setEyeBusy] = useState({});   // uid → true enquanto busca o valor
  const [eyeError, setEyeError] = useState('');
  const timers = useRef({});                    // uid → id do setTimeout que reoculta

  // Nenhum valor revelado sobrevive ao fechar o formulário.
  useEffect(() => () => { Object.values(timers.current).forEach(clearTimeout); }, []);

  function scheduleHide(uid) {
    clearTimeout(timers.current[uid]);
    timers.current[uid] = setTimeout(() => {
      setForm((prev) => hideField(prev, uid));
    }, REVEAL_SECONDS * 1000);
  }

  async function toggleEye(f) {
    setEyeError('');
    if (f.visible) {
      clearTimeout(timers.current[f.uid]);
      setForm((prev) => hideField(prev, f.uid));
      return;
    }
    if (eyeAction(f) === 'toggle') {
      setForm((prev) => updateField(prev, f.uid, { visible: true }));
      scheduleHide(f.uid);
      return;
    }
    setEyeBusy((prev) => ({ ...prev, [f.uid]: true }));
    const res = await revealCredentialField(editing.name, f.key);
    setEyeBusy((prev) => ({ ...prev, [f.uid]: false }));
    if (res && res.ok) {
      setForm((prev) => applyReveal(prev, f.uid, res.data.value));
      scheduleHide(f.uid);
    } else {
      setEyeError((res && res.error) || 'Não foi possível ver o valor.');
    }
  }

  const check = validateCredentialForm(form, { isNew });
  const fe = check.errors.fieldErrors || {};
  const showErr = touched && !check.ok;

  function toggleTool(name) {
    setTools((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  }

  function submit() {
    setTouched(true);
    if (!check.ok || busy) return;
    onSave(editing ? editing.name : form.name.trim(), credentialPayload(form), [...tools]);
  }

  // Tool que o vínculo aponta mas já não existe (P4: o vínculo é só um nome).
  const known = new Set(codeTools.map((t) => t.name));
  const orphan = [...tools].filter((t) => !known.has(t));

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">
        ${editing
          ? html`Editar credencial <code class="text-[12px] text-wa-secondary">${editing.name}</code>`
          : 'Nova credencial'}
      </div>
      <div class="flex flex-col gap-3">
        ${isNew ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Nome</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] font-mono" type="text"
              placeholder="ex: plataforma_redes" value=${form.name} autocomplete="off"
              onInput=${(e) => setForm((prev) => ({ ...prev, name: e.target.value }))} />
            ${showErr && check.errors.name ? html`<div class="text-[12px] text-red-500 mt-1">${check.errors.name}</div>` : null}
            <div class="text-[11px] text-wa-secondary mt-1">
              No código da tool: <code class="font-mono">ctx.credential("${form.name.trim() || 'nome'}")</code>${' '}devolve os campos.
            </div>
          </div>
        ` : null}

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Descrição</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="text"
            placeholder="Para que serve" value=${form.description} autocomplete="off"
            onInput=${(e) => setForm((prev) => ({ ...prev, description: e.target.value }))} />
        </div>

        <div>
          <div class="flex items-center justify-between mb-1">
            <label class="text-[12px] text-wa-secondary">Campos</label>
            <button type="button" class="text-[12px] text-wa-teal hover:underline"
              onClick=${() => setForm((prev) => addField(prev))}>+ Adicionar campo</button>
          </div>
          ${showErr && check.errors.fields ? html`<div class="text-[12px] text-red-500 mb-1">${check.errors.fields}</div>` : null}
          <div class="flex flex-col gap-2">
            ${form.fields.map((f) => html`
              <div key=${f.uid} class="border border-wa-border rounded-md p-2 bg-wa-bg">
                <div class="flex gap-2 flex-wrap items-start">
                  <input class="wa-field px-2 py-1.5 rounded-md text-[13px] font-mono w-36 shrink-0" type="text"
                    placeholder="campo" value=${f.key} autocomplete="off" disabled=${f.existing}
                    onInput=${(e) => setForm((prev) => updateField(prev, f.uid, { key: e.target.value }))} />
                  <div class="flex-1 min-w-[10rem]">
                    <input class="wa-field w-full px-2 py-1.5 rounded-md text-[13px] font-mono"
                      type=${f.secret && !f.visible ? 'password' : 'text'}
                      autocomplete=${f.secret ? 'new-password' : 'off'}
                      placeholder=${f.existing && f.secret && f.hasValue ? '•••••••• (deixe vazio para manter)' : 'valor'}
                      value=${f.value}
                      onInput=${(e) => setForm((prev) => updateField(prev, f.uid, { value: e.target.value }))} />
                  </div>
                  ${f.secret ? html`
                    <button type="button" disabled=${!!eyeBusy[f.uid]}
                      title=${f.visible ? 'Ocultar o valor' : 'Mostrar o valor'}
                      aria-label=${f.visible ? 'Ocultar o valor' : 'Mostrar o valor'}
                      aria-pressed=${f.visible ? 'true' : 'false'}
                      class="text-wa-secondary hover:text-wa-text p-1.5 shrink-0 disabled:opacity-50"
                      onClick=${() => toggleEye(f)}><${EyeIcon} off=${f.visible} /></button>
                  ` : null}
                  <label class="flex items-center gap-1 text-[12px] text-wa-secondary shrink-0 mt-1.5">
                    <input type="checkbox" checked=${f.secret}
                      onChange=${(e) => setForm((prev) => updateField(prev, f.uid, { secret: e.target.checked }))} />
                    secreto
                  </label>
                  <button type="button" title="Remover campo"
                    class="text-wa-secondary hover:text-red-500 text-lg leading-none px-1 shrink-0"
                    onClick=${() => setForm((prev) => removeField(prev, f.uid))}>×</button>
                </div>
                <div class="flex items-center gap-2 mt-1">
                  <span class="text-[11px] text-wa-secondary">${fieldStatus(f)}</span>
                  ${showErr && fe[f.uid] ? html`<span class="text-[12px] text-red-500">${fe[f.uid]}</span>` : null}
                </div>
              </div>
            `)}
          </div>
          ${eyeError ? html`<div class="text-[12px] text-red-500 mt-1">${eyeError}</div>` : null}
          <div class="text-[11px] text-wa-secondary mt-2">
            Campo <b>secreto</b> não aparece na lista; use o <b>olho</b> para ver o valor (cada vez que
            você olha, fica registrado quem viu, e ele se oculta sozinho em ${REVEAL_SECONDS}s).
            Valores curtos (ex.: 5 dígitos) são
            mascarados com <code class="font-mono">***</code> em <b>qualquer</b> trecho igual na resposta da tool —
            um ID que contenha o valor volta corrompido.
          </div>
        </div>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Tools que podem usar esta credencial</label>
          ${codeTools.length === 0 && orphan.length === 0 ? html`
            <div class="text-[12px] text-wa-secondary">Nenhuma tool de código cadastrada.</div>
          ` : null}
          <div class="flex flex-wrap gap-2">
            ${codeTools.map((t) => html`
              <label key=${t.name} class="flex items-center gap-1.5 text-[12px] text-wa-text border border-wa-border rounded-md px-2 py-1 cursor-pointer hover:bg-wa-hover">
                <input type="checkbox" checked=${tools.has(t.name)} onChange=${() => toggleTool(t.name)} />
                <code class="font-mono">${t.name}</code>
              </label>
            `)}
            ${orphan.map((name) => html`
              <label key=${name} class="flex items-center gap-1.5 text-[12px] text-amber-700 dark:text-amber-400 border border-amber-500/40 rounded-md px-2 py-1 cursor-pointer">
                <input type="checkbox" checked=${true} onChange=${() => toggleTool(name)} />
                <code class="font-mono">${name}</code> <span>(tool inexistente — desmarque para remover)</span>
              </label>
            `)}
          </div>
          <div class="text-[11px] text-wa-secondary mt-1">Só tools de código recebem credencial.</div>
        </div>

        ${serverError ? html`<div class="text-[13px] text-red-600">${serverError}</div>` : null}
        <div class="flex gap-2 justify-end">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onCancel} disabled=${busy}>Cancelar</button>
          <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick=${submit} disabled=${busy}>${busy ? 'Salvando…' : 'Salvar'}</button>
        </div>
      </div>
    </div>
  `;
}

export default function CredentialsPanel({ codeTools = [] }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [formError, setFormError] = useState('');
  const [editing, setEditing] = useState(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmDel, setConfirmDel] = useState(null);

  // Só as tools de código: são as únicas que o runner isolado alimenta.
  const code = codeTools.filter((t) => (t.kind || 'code') === 'code');

  async function load() {
    setLoading(true);
    setError('');
    const res = await listCredentials();
    if (res && res.ok) { setItems(res.data || []); setLoaded(true); }
    else setError((res && res.error) || 'Falha ao carregar credenciais.');
    setLoading(false);
  }

  useEffect(() => { if (open && !loaded) load(); }, [open]);

  async function handleSave(name, body, tools) {
    setBusy(true); setFormError('');
    const res = await saveCredential(name, body);
    if (!(res && res.ok)) {
      setBusy(false);
      setFormError((res && res.error) || 'Falha ao salvar a credencial.');
      return;
    }
    const linked = await setCredentialTools(name, tools);
    setBusy(false);
    if (!(linked && linked.ok)) {
      // A credencial JÁ foi salva; só o vínculo falhou — não esconda isso.
      setFormError(`Credencial salva, mas o vínculo com as tools falhou: ${(linked && linked.error) || 'erro'}`);
      load();
      return;
    }
    setEditing(null); setCreating(false); load();
  }

  async function handleDelete() {
    const name = confirmDel;
    if (!name) return;
    setBusy(true);
    const res = await deleteCredential(name);
    setBusy(false); setConfirmDel(null);
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao excluir a credencial.');
  }

  const showForm = creating || editing;

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg mb-4">
      <button class="w-full flex items-center justify-between px-3 py-2.5 text-left"
        onClick=${() => setOpen(!open)}>
        <span class="text-[14px] font-medium text-wa-text">Credenciais das tools</span>
        <span class="text-[12px] text-wa-secondary">${open ? 'ocultar' : 'mostrar'}</span>
      </button>

      ${open ? html`
        <div class="px-3 pb-3 border-t border-wa-border pt-3">
          <div class="text-[12px] text-wa-secondary mb-3">
            Guarde aqui chaves de API e URLs que as tools de código usam, em vez de colá-las no código
            (o código fica versionado no histórico). Campo secreto não aparece na lista; use o <b>olho</b>${' '}para ver o valor (fica registrado quem viu). A tool lê com <code class="font-mono">ctx.credential("nome")</code>.
          </div>
          <div class="bg-amber-500/10 border border-amber-500/30 rounded-md px-3 py-2 mb-3 text-[12px] text-amber-700 dark:text-amber-400">
            A liberação por tool organiza e deixa rastro, mas <b>não é barreira</b>: o código de uma tool de código
            roda com acesso ao banco. Quem pode editar tools alcança qualquer credencial.
          </div>

          ${error ? html`<div class="text-red-600 mb-2 text-sm">${error}</div>` : null}

          ${showForm ? html`
            <${CredentialForm}
              key=${editing ? editing.name : 'new'}
              editing=${editing}
              codeTools=${code}
              busy=${busy}
              serverError=${formError}
              onSave=${handleSave}
              onCancel=${() => { setEditing(null); setCreating(false); setFormError(''); }} />
          ` : html`
            <div class="flex justify-end mb-2">
              <button class="px-3 py-1.5 rounded-md text-[13px] text-white bg-wa-teal hover:opacity-90 transition-opacity"
                onClick=${() => { setCreating(true); setEditing(null); setFormError(''); }}>+ Nova credencial</button>
            </div>
          `}

          ${loading ? html`<div class="text-wa-secondary text-sm">Carregando…</div>` : null}
          ${!loading && loaded && items.length === 0 ? html`
            <div class="text-wa-secondary text-sm">Nenhuma credencial cadastrada.</div>` : null}

          <div class="flex flex-col gap-2">
            ${items.map((c) => html`
              <div key=${c.name} class="border border-wa-border rounded-md p-2.5 bg-wa-bg">
                <div class="flex items-start gap-2 flex-wrap">
                  <div class="flex-1 min-w-0">
                    <code class="text-[13px] text-wa-text font-medium">${c.name}</code>
                    ${c.description ? html`<div class="text-[12px] text-wa-secondary">${c.description}</div>` : null}
                    <div class="flex flex-wrap gap-1.5 mt-1.5">
                      ${c.fields.map((f) => html`
                        <span key=${f.key} class="text-[11px] px-1.5 py-0.5 rounded bg-wa-hover text-wa-text font-mono">
                          ${f.key}${f.secret ? ' · secreto' : ''}${f.secret ? (f.has_value ? ' · definido' : ' · vazio') : ` = ${f.value}`}
                        </span>
                      `)}
                    </div>
                    <div class="text-[11px] text-wa-secondary mt-1.5">
                      Tools: ${c.tools.length
                        ? c.tools.map((t, i) => html`${i ? ', ' : ''}<code class="font-mono">${t}</code>${(c.missing_tools || []).includes(t) ? ' (inexistente)' : ''}`)
                        : 'nenhuma'}
                    </div>
                    <div class="text-[11px] text-wa-secondary">
                      Definida${c.updated_by_name ? html` · alterada por ${c.updated_by_name}` : null} em ${fmtDate(c.updated_at)}
                    </div>
                  </div>
                  <div class="flex gap-1 shrink-0">
                    <button class="px-2 py-1 rounded-md text-[12px] text-wa-text hover:bg-wa-hover transition-colors"
                      onClick=${() => { setEditing(c); setCreating(false); setFormError(''); }}>Editar</button>
                    <button class="px-2 py-1 rounded-md text-[12px] text-red-600 hover:bg-wa-hover transition-colors"
                      onClick=${() => setConfirmDel(c.name)}>Excluir</button>
                  </div>
                </div>
              </div>
            `)}
          </div>
        </div>
      ` : null}

      ${confirmDel ? html`
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick=${() => setConfirmDel(null)}>
          <div class="bg-wa-bg border border-wa-border rounded-lg p-5 w-full max-w-sm" onClick=${(e) => e.stopPropagation()}>
            <div class="text-[15px] font-medium text-wa-text mb-2">Excluir credencial</div>
            <div class="text-[13px] text-wa-secondary mb-4 break-words">
              Excluir <code class="font-mono">${confirmDel}</code>? As tools que a usam passam a falhar até
              receberem outra. Isso não pode ser desfeito.
            </div>
            <div class="flex gap-2 justify-end">
              <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => setConfirmDel(null)} disabled=${busy}>Cancelar</button>
              <button class="px-4 py-2 rounded-md text-[14px] text-white bg-red-600 hover:opacity-90 transition-opacity disabled:opacity-50"
                onClick=${handleDelete} disabled=${busy}>${busy ? 'Aguarde…' : 'Excluir'}</button>
            </div>
          </div>
        </div>
      ` : null}
    </div>
  `;
}
