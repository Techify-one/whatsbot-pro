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
        try { const c = await getConversation(atend.id); cur = (c && c.ok && c.data) ? c.data.assignee_user_id : null; }
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
    try {
      const j = await api.http.post(`/atendimentos/${atend.id}/resolve`, { fields: result.fields || {} });
      if (j && j.ok && j.data && j.data.protocolo_id != null) protocoloId = j.data.protocolo_id;
    } catch (_) { /* ignore — o before_status do backend é a rede de segurança */ }

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

  // 4) Popup "vincular ao protocolo anterior" (plano 49): ao ABRIR uma conversa cujo
  //    contato fechou um protocolo há pouco (janela configurável no backend), pergunta
  //    se este atendimento faz parte do anterior. O evento ui.conversation.opened
  //    dispara a CADA mount do ContactDetail, então um guard em memória por previous.id
  //    evita repetir na mesma sessão (P7/gotcha 5). Tudo best-effort: nunca quebra a
  //    abertura da conversa.
  const askedRelink = new Set();   // previous.id já perguntados nesta sessão
  api.on('ui.conversation.opened', async ({ conversationId, phone }) => {
    try {
      if (!can('edit')) return;                    // sem editar → não oferece o vínculo
      if (!conversationId) return;
      // O evento traz conversationId/phone; a sugestão é por contato → resolve o contato.
      let contactId = null;
      try {
        const c = await getConversation(conversationId);
        contactId = (c && c.ok && c.data) ? c.data.contact_id : null;
      } catch (_) { contactId = null; }
      if (contactId == null) return;

      const s = await getJson(`${apiBase}/contacts/${contactId}/relink-suggestion`);
      const data = (s && s.ok && s.data) || null;
      if (!data || !data.suggest || !data.previous) return;   // fora da janela / desligado
      const prevId = data.previous.id;
      if (askedRelink.has(prevId)) return;         // já perguntei por este anterior
      askedRelink.add(prevId);
      const currentOpenId = (data.current_open && data.current_open.id) || null;

      const doRelink = () => api.http.post(`/protocolos/${prevId}/relink`,
        { current_open_id: currentOpenId });
      const doCloseAll = () => resolveAndCloseAll(api, apiBase, conversationId, currentOpenId);

      const outcome = await api.ui.openModal((close) => html`
        <${RelinkModal} previous=${data.previous} secondsSinceClose=${data.seconds_since_close}
          doRelink=${doRelink} doCloseAll=${doCloseAll} onDone=${(o) => close(o)} />`);

      // (a) sucesso: deep-link opcional pra aba Protocolos abrindo o anterior reaberto
      //     (mesmo precedente do "Resolver e ir ao protocolo").
      if (outcome === 'relinked') {
        history.pushState(null, '', `/protocolos?detail=${prevId}`);
        window.dispatchEvent(new PopStateEvent('popstate'));
      }
    } catch (_) { /* popup best-effort — a abertura da conversa nunca é bloqueada */ }
  });
}
