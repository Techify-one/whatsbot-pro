// Módulo de extensão de frontend do plugin Atendimentos. O app importa este
// arquivo UMA vez no boot e chama o default export register(api). Tudo que o
// plugin faz na tela é registrado aqui contra o registry compartilhado — nada
// fica no core, e desativar o plugin remove tudo no próximo boot.
//
// Primitivas usadas:
//   1. filter — filter.conversation.beforeResolve → popup com campos configuráveis
//   2. route  — overrideRoute('attendances') → mantém kanban/lista + lista de atendimentos
//   3. slot   — gear.menu.items → atalho para a aba Atendimentos
// (A entidade Atendimento — atual/histórico/conversas — é toda gerida na aba Atendimentos
//  kanban/lista; por isso NÃO há mais painel injetado no ℹ️ da conversa.)

import { h } from 'preact';
import htm from 'htm';
import { ResolveForm } from '/plugins/atendimentos/static/resolve_form.js';
import { AtendimentosTab } from '/plugins/atendimentos/static/atendimentos_tab.js';
// Helpers do core p/ ler as definições de atributos de conversa e gravar os valores
// (mesma rota da aba "Informações da conversa"). Carregam auth como qualquer chamada core.
import { getCustomAttributes, updateConversationInfo } from '/static/js/services/api.js';

const html = htm.bind(h);

export default function register(api) {
  const apiBase = api.apiBase;                 // /api/plugins/atendimentos
  const { authHeaders } = api.services;

  async function getJson(url) {
    const r = await fetch(url, { headers: authHeaders() });
    return r.json();
  }

  // 1) Popup ao resolver conversa — pré-preenchido. Roda em TODOS os 5 sites de
  //    "Resolver" porque o core os afunila por resolveConversation. Mostra os rótulos do
  //    atendimento (escopo conversa) E os atributos personalizados do core, já preenchidos
  //    com o que está na conversa (mesma fonte da aba "Informações da conversa").
  api.addFilter('filter.conversation.beforeResolve', async (ctx, conv) => {
    // (a) Rótulos editáveis do atendimento: OBS (fixo editável) + extras. Os fixos de
    //     sistema (Início/Fim/Atendente/ID) são preenchidos pelo fluxo, não digitados.
    let defs = [];
    try {
      const d = await getJson(`${apiBase}/field-defs?scope=conversa`);
      defs = ((d && d.ok && d.data && d.data.defs) || []).filter((x) => !x.readonly);
    } catch (_) { /* sem defs → segue sem essa seção */ }

    // (b) Atributos personalizados de conversa criados manualmente (is_system=0). Os do
    //     plugin já chegam espelhados como is_system=1 e aparecem na seção (a) — não duplicar.
    let attrDefs = [];
    try {
      const a = await getCustomAttributes('conversation');
      attrDefs = ((a && a.ok && Array.isArray(a.data)) ? a.data : []).filter((x) => !x.is_system);
    } catch (_) { /* sem atributos → segue sem essa seção */ }

    // Valores atuais p/ pré-preencher (o que foi salvo na aba "Informações da conversa").
    const initialValues = (conv && conv.custom_attributes) || {};

    let result = { fields: {}, custom_attributes: {} };
    if (defs.length || attrDefs.length) {
      const r = await api.ui.openModal((close) => html`
        <${ResolveForm} defs=${defs} attrDefs=${attrDefs} initialValues=${initialValues}
          onOk=${(v) => close(v)} onCancel=${() => close(null)} />`);
      if (!r) return null;                       // cancelado → aborta o fechar
      result = r;
    }

    // 1) Salva os atributos personalizados manuais no core (mesma rota da aba de info) —
    //    persiste is_system=0 e satisfaz o gate de obrigatórios do core.
    try {
      const ca = result.custom_attributes || {};
      if (Object.keys(ca).length) await updateConversationInfo(conv.id, { custom_attributes: ca });
    } catch (_) { /* ignore — o gate do core/plugin é a rede de segurança */ }

    // 2) Grava resultado/observação + FIM no vínculo (cria o vínculo se o evento de
    //    auto-link ainda não rodou). O before_status do backend ainda barra se faltar required.
    try {
      await fetch(`${apiBase}/conversas/${conv.id}/resolve`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ fields: result.fields || {} }),
      });
    } catch (_) { /* ignore — o before_status do backend é a rede de segurança */ }
    return conv;                                 // segue → o core chama /status
  });

  // 2) Override da aba Atendimentos: o componente reusa o Atendimentos nativo
  //    (kanban/lista) e adiciona a visão "Atendimentos". Desativar → tab nativa volta.
  api.overrideRoute('attendances', (props) => html`<${AtendimentosTab} ...${props} api=${api} />`);

  // 3) Entrada no menu da engrenagem p/ ir à aba Atendimentos (o item nativo fica
  //    escondido enquanto a aba está sob override). Herdado do ext_demo.
  api.addSlot('gear.menu.items', ({ onTabChange, close }) => html`
    <a href="/atendimentos"
      onClick=${(e) => { e.preventDefault(); onTabChange('attendances'); if (close) close(); }}
      class="w-full text-left px-4 py-2.5 text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-2 no-underline text-wa-text">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M4 5h6v6H4V5zm0 8h6v6H4v-6zm8-8h8v6h-8V5zm0 8h8v6h-8v-6z"/></svg>
      Atendimentos
    </a>`);
}
