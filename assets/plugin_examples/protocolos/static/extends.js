// Módulo de extensão de frontend do plugin Protocolos. O app importa este
// arquivo UMA vez no boot e chama o default export register(api). Tudo que o
// plugin faz na tela é registrado aqui contra o registry compartilhado — nada
// fica no core, e desativar o plugin remove tudo no próximo boot.
//
// Primitivas usadas:
//   1. filter — filter.conversation.beforeResolve → popup com campos configuráveis
//   2. route  — overrideRoute('attendances') → mantém kanban/lista + lista de protocolos
//   3. slot   — gear.menu.items → atalho para a aba Protocolos
// (A entidade Protocolo — atual/histórico/atendimentos — é toda gerida na aba Protocolos
//  kanban/lista; por isso NÃO há mais painel injetado no ℹ️ da atendimento.)

import { h } from 'preact';
import htm from 'htm';
import { ResolveForm } from '/plugins/protocolos/static/resolve_form.js';
import { RelinkModal } from '/plugins/protocolos/static/relink_modal.js';
import { ProtocolosTab } from '/plugins/protocolos/static/protocolos_tab.js';
// Helper do core p/ resolver o assignee atual da conversa (seed do rótulo "atendente").
// Carrega auth como qualquer chamada core.
import { getConversation } from '/static/js/services/api.js';
// Toast do core (barramento global — ver services/notify.js): avisa que o resolver não
// aconteceu porque a escolha foi "é um novo protocolo".
import { notify } from '/static/js/services/notify.js';

const html = htm.bind(h);

// ID do usuário conectado (localStorage do core) — semeia os campos "atendente" com o
// operador atual como valor padrão. null quando não há sessão identificada.
function currentUserId() {
  try { const u = JSON.parse(localStorage.getItem('whatsbot_user') || 'null'); return (u && u.id != null) ? u.id : null; }
  catch (_) { return null; }
}
// Usuário conectado completo (com permissions[]) — usado no gate de permissão.
function currentUser() {
  try { return JSON.parse(localStorage.getItem('whatsbot_user') || 'null'); }
  catch (_) { return null; }
}

// Encadeamento "fechar conversa e protocolo juntos" (botão c do popup de vínculo) —
// replica o forceResolveAndClose da aba Protocolos, sem depender do componente: resolve
// o ciclo aberto (abrindo o popup de campos se houver obrigatórios), fecha a conversa no
// core (status=closed) e finaliza o protocolo. Retorna { ok, error? }.
async function resolveAndCloseAll(api, apiBase, conversationId, protocoloId) {
  let fields = {};
  try {
    const d = await api.http.get(`${apiBase}/field-defs?scope=atendimento`);
    const defs = ((d && d.ok && d.data && d.data.defs) || [])
      .filter((x) => !x.readonly && x.type !== 'atendente');
    if (defs.length) {
      const picked = await api.ui.openModal((close) => html`
        <${ResolveForm} defs=${defs} initialValues=${{}} defaultAssignee=${currentUserId()}
          onOk=${(v) => close(v)} onCancel=${() => close(null)} />`);
      if (!picked) return { ok: false, error: 'Cancelado.' };
      fields = picked.fields || {};
    }
  } catch (_) { /* sem defs → segue com fields vazio */ }
  // 1) resolve o CICLO (Fim + campos). 2) fecha a conversa no core. 3) finaliza o protocolo.
  const res = await api.http.post(`/atendimentos/${conversationId}/resolve`, { fields });
  if (res && res.ok === false) return res;
  const st = await api.services.setConversationStatus(conversationId, 'closed');
  if (st && st.ok === false) return { ok: false, error: st.error || 'Falha ao fechar a conversa.' };
  const pid = protocoloId || (res && res.data && res.data.protocolo_id) || null;
  if (pid) {
    const closed = await api.http.post(`/protocolos/${pid}/close`);
    if (closed && closed.ok === false) return closed;
  }
  return { ok: true };
}

// contact_id da conversa: alguns call sites de "Resolver" passam só { id }.
async function contactIdOf(atend) {
  if (atend && atend.contact_id != null) return atend.contact_id;
  try {
    const c = await getConversation(atend.id);
    const cv = (c && c.ok && c.data) ? c.data.conversation : null;
    return cv ? cv.contact_id : null;
  } catch (_) { return null; }
}

// Decisão de CONTINUIDADE no momento de resolver o atendimento. Devolve
// `{ outcome, pending }`:
//   outcome 'resolved' → fundido no protocolo anterior (nenhum atendimento novo; o
//                        protocolo segue finalizado) — o core só precisa fechar a conversa
//   outcome 'form'     → segue para o formulário padrão de resolver
//   outcome 'abort'    → nada é resolvido: o atendente cancelou OU escolheu "é um novo
//                        protocolo" (que abre o protocolo/atendimento novo e mantém a
//                        conversa ABERTA para o caso novo ser tocado)
//   pending            → decisão a APLICAR depois que o atendimento for resolvido
//                        (`applyContinuity`), ou null
//
// As duas escolhas do popup encerram a pergunta na hora — nenhuma delas passa pelo
// formulário de resolver. Cancelar não deixa efeito e a pergunta volta na próxima
// tentativa. Fora da janela, sem protocolo anterior ou com qualquer falha → 'form' (nunca
// trava o atendente: na dúvida, o fluxo padrão de sempre).
async function resolveContinuity(api, apiBase, atend) {
  const skip = { outcome: 'form', pending: null };
  let sug = null;
  try {
    const contactId = await contactIdOf(atend);
    if (contactId == null) return skip;
    const r = await api.http.get(
      `${apiBase}/contacts/${contactId}/relink-suggestion?conversation_id=${atend.id}`);
    sug = (r && r.ok && r.data) || null;
  } catch (_) { return skip; }
  if (!sug) return skip;

  // A decisão vem do atributo personalizado (não pergunta) ou do clique do atendente.
  let kind = null;
  let source = 'user';
  if (sug.attr_decision) { kind = sug.attr_decision; source = 'attr'; }
  else if (sug.suggest && sug.previous) {
    const picked = await api.ui.openModal((close) => html`
      <${RelinkModal} previous=${sug.previous} secondsSinceClose=${sug.seconds_since_close}
        onPick=${(v) => close(v)} />`);
    if (picked !== 'previous' && picked !== 'new') return { outcome: 'abort', pending: null };
    kind = picked;
  }
  if (kind !== 'previous' && kind !== 'new') return skip;

  const pending = { kind, previous_id: (sug.previous && sug.previous.id) || null, source };

  // "É um novo protocolo": abre de fato o protocolo novo + o atendimento (ciclo) dele e
  // ABORTA o resolver — o caso é novo, então a conversa continua ABERTA para ser tocada.
  // Sem formulário e sem fechar nada. Falha → cai no fluxo padrão de resolver.
  if (kind === 'new') {
    const opened = await applyContinuity(api, apiBase, atend.id, pending);
    // Falhou → fluxo padrão de resolver, SEM reaplicar depois: abrir o ciclo já com o
    // atendimento resolvido deixaria um atendimento aberto fantasma.
    if (opened && opened.ok === false) return { outcome: 'form', pending: null };
    notify('Novo protocolo aberto — o atendimento segue em aberto.', { kind: 'success' });
    return { outcome: 'abort', pending: null };
  }

  // "Faz parte do anterior": nada de novo aconteceu. NENHUM pop-up adicional e NENHUM
  // /resolve — o backend funde este re-engajamento no protocolo anterior (descarta o
  // protocolo transitório + seus ciclos, estende a data final do último atendimento já
  // existente e mantém o protocolo finalizado, com os rótulos como estavam). Ao core resta
  // só fechar a conversa. Falha → cai no formulário padrão (nunca trava o atendente).
  const done = await applyContinuity(api, apiBase, atend.id, pending);
  if (done && done.ok === false) return { outcome: 'form', pending };
  return { outcome: 'resolved', pending: null };
}

// Aplica a decisão de continuidade: kind='previous' funde este re-engajamento no protocolo
// anterior (sem atendimento novo, protocolo segue finalizado); kind='new' abre o protocolo
// novo + o atendimento dele. Nos dois casos também grava/consome o atributo personalizado.
// Devolve a resposta HTTP (ou `{ok:false}` quando a chamada falhou) — quem chama usa isso
// para cair no formulário padrão de resolver.
async function applyContinuity(api, apiBase, conversationId, pending) {
  if (!pending) return null;
  try {
    return await api.http.post(`${apiBase}/conversas/${conversationId}/relink-decision`, pending);
  } catch (_) { return { ok: false }; }
}

export default function register(api) {
  const apiBase = api.apiBase;                 // /api/plugins/protocolos
  // Permissão do plugin (default-allow p/ legado/sem RBAC). `edit` gate o popup de
  // resolver (e o POST /resolve, que 403aria); `view` gate o atalho no menu.
  const can = (key) => (api.services.hasPermission
    ? api.services.hasPermission(currentUser(), `plugin.protocolos.${key}`) : true);

  // Via api.http do core: checa status HTTP e toasta "Permissão negada." em 403.
  const getJson = (url) => api.http.get(url);

  // 1) Popup ao resolver atendimento — pré-preenchido. Roda em TODOS os 5 sites de
  //    "Resolver" porque o core os afunila por resolveConversation. Mostra APENAS os
  //    rótulos do protocolo (escopo atendimento) — os atributos personalizados de
  //    CONVERSA do core não fazem mais parte do plugin (ficam só no core).
  api.addFilter('filter.conversation.beforeResolve', async (ctx, atend) => {
    // Sem permissão de EDITAR protocolos → não injeta o popup nem chama /resolve
    // (que 403aria). O core segue e resolve a conversa normalmente.
    if (!can('edit')) return atend;

    // 0) CONTINUIDADE (antes de tudo): o contato fechou um protocolo há pouco? A decisão
    //    "faz parte do anterior × é um novo protocolo" acontece AQUI, ao resolver — não
    //    mais ao abrir a conversa. "Faz parte do anterior" funde tudo no protocolo anterior
    //    e fecha; "É um novo protocolo" abre o protocolo/atendimento novo e deixa a conversa
    //    aberta. Nenhuma das duas passa pelo formulário (nem com rótulos obrigatórios), e
    //    cancelar não deixa efeito — a pergunta volta. Fora da janela nada disso aparece.
    const cont = await resolveContinuity(api, apiBase, atend);
    if (cont.outcome === 'abort') return null;      // cancelou → não resolve nada
    if (cont.outcome === 'resolved') return atend;  // vinculado ao anterior → core fecha

    // Rótulos editáveis do protocolo: OBS (fixo editável) + extras. Os fixos de
    // sistema (Início/Fim/Atendente/ID) são preenchidos pelo fluxo, não digitados.
    let defs = [];
    try {
      const d = await getJson(`${apiBase}/field-defs?scope=atendimento`);
      defs = ((d && d.ok && d.data && d.data.defs) || []).filter((x) => !x.readonly);
    } catch (_) { /* sem defs → segue sem essa seção */ }

    // Valores atuais p/ pré-preencher os rótulos do plugin (chegam espelhados em
    // conversations.custom_attributes) + o seed do atendente logo abaixo.
    const initialValues = { ...((atend && atend.custom_attributes) || {}) };

    // Semear o rótulo "Atendente (nativo)" com o assignee ATUAL da conversa. Alguns call
    // sites passam só { id } → busca no core quando o objeto não traz assignee_user_id.
    // (Sem seed, resolver poderia LIMPAR a atribuição existente — o backend só reatribui
    //  quando o valor muda, então seed correto = re-asserção idempotente.)
    const atendenteDef = defs.find((d) => d.type === 'atendente');
    if (atendenteDef) {
      let cur = atend && atend.assignee_user_id;
      if (cur === undefined) {
        try { const c = await getConversation(atend.id); const cv = (c && c.ok && c.data) ? c.data.conversation : null; cur = cv ? cv.assignee_user_id : null; }
        catch (_) { cur = null; }
      }
      initialValues[atendenteDef.key] = (cur == null ? '' : cur);
    }

    let result = { fields: {} };
    if (defs.length) {
      const r = await api.ui.openModal((close) => html`
        <${ResolveForm} defs=${defs} initialValues=${initialValues}
          defaultAssignee=${currentUserId()}
          onOk=${(v) => close(v)} onCancel=${() => close(null)} />`);
      if (!r) return null;                       // cancelado → aborta o fechar
      result = r;
    }

    // 1) Grava resultado/observação + FIM no vínculo (cria o vínculo se o evento de
    //    auto-link ainda não rodou). O before_status do backend ainda barra se faltar
    //    required. LÊ a resposta p/ capturar o protocolo_id (liga atendimento→protocolo;
    //    vem de get_latest_cycle/_atendimento_dict, que inclui a coluna protocolo_id).
    let protocoloId = null;
    let resolveOk = false;
    try {
      const j = await api.http.post(`/atendimentos/${atend.id}/resolve`, { fields: result.fields || {} });
      resolveOk = !!(j && j.ok);
      if (j && j.ok && j.data && j.data.protocolo_id != null) protocoloId = j.data.protocolo_id;
    } catch (_) { /* ignore — o before_status do backend é a rede de segurança */ }

    // 1b) Só AGORA a decisão de continuidade tem efeito (vínculo ao anterior + gravação do
    //     atributo): o atendimento foi de fato resolvido. Cancelar o formulário acima já
    //     abortou tudo, e a pergunta volta na próxima tentativa.
    if (resolveOk && cont.pending) {
      await applyContinuity(api, apiBase, atend.id, cont.pending);
      if (cont.pending.kind === 'previous' && cont.pending.previous_id != null) {
        protocoloId = cont.pending.previous_id;    // "ir ao protocolo" = o anterior
      }
    }

    // 2) "Resolver e ir ao protocolo" (result.goTo): navega à aba Protocolos abrindo
    //    o detalhe daquele protocolo. Contido no plugin — o core resolve a aba pelo
    //    pathname /protocolos (tabFromPathPure; /attendances segue como alias) e o
    //    ProtocolosList lê ?detail no mount/popstate (precedente: o chat lê ?message
    //    via scrollMsgFromSearchStr).
    if (result.goTo && protocoloId != null) {
      history.pushState(null, '', `/protocolos?detail=${protocoloId}`);
      window.dispatchEvent(new PopStateEvent('popstate'));
    }
    return atend;                                 // segue → o core chama /status
  });

  // 2) Override da aba Protocolos: o componente reusa o Protocolos nativo
  //    (kanban/lista) e adiciona a visão "Protocolos". Desativar → tab nativa volta.
  api.overrideRoute('attendances', (props) => html`<${ProtocolosTab} ...${props} api=${api} />`);

  // 3) Entrada no menu da engrenagem p/ ir à aba Protocolos (o item nativo fica
  //    escondido enquanto a aba está sob override). Herdado do ext_demo.
  api.addSlot('gear.menu.items', ({ onTabChange, close }) => (can('view') ? html`
    <a href="/protocolos"
      onClick=${(e) => { e.preventDefault(); onTabChange('attendances'); if (close) close(); }}
      class="w-full text-left px-4 py-2.5 text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-2 no-underline text-wa-text">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M4 5h6v6H4V5zm0 8h6v6H4v-6zm8-8h8v6h-8V5zm0 8h8v6h-8v-6z"/></svg>
      Protocolos
    </a>` : null));

  // (A continuidade de protocolo NÃO tem mais overlay no chat: abrir uma conversa fechada
  //  não pergunta nem reabre nada. A pergunta acontece ao RESOLVER o atendimento — ver
  //  `resolveContinuity` no filtro beforeResolve acima.)
}
