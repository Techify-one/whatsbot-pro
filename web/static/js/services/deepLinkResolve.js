// @ts-check
//
// Como uma URL de deep-link (`/conversations/:id` · `/contacts/:id`) vira uma
// seleção no hub — plano 89 · F1/F2.
//
// A decisão morava dentro de um efeito do `useConversationSelection`, misturada
// com os setters de estado e com os carimbos (`lastResolvedConvId`/`lastResolvedId`),
// e não tinha NENHUM teste. Foi assim que o guard `contacts.length === 0` — copiado
// verbatim do ramo de CONTATO para o ramo de CONVERSA em `288d686`, no mesmo commit
// que criou o ramo `else` que o torna desnecessário — passou um ano quebrando, em
// silêncio, todo link de conversa aberto com a sidebar vazia (aba "Minhas" sem
// atribuições, chip "Resolvidas" sem resolvidas, falha de rede na lista): nenhuma
// requisição era feita, a URL ficava intacta e o painel ficava no placeholder.
//
// Aqui a regra existe uma vez, decide sem tocar em estado, e é testável.
//
// PURO: sem preact, sem DOM, sem rede, sem estado de módulo.

/**
 * @typedef {Object} DeepLinkInput
 * @property {number|null} [initialConversationId] - id de `/conversations/:id`
 * @property {number|null} [initialContactId] - id de `/contacts/:id` (legado)
 * @property {Record<string, any>[]} [contacts] - linhas CRUAS da sidebar (não as filtradas)
 * @property {boolean} [loading] - a lista ainda está em voo
 * @property {number|null} [lastResolvedConvId] - carimbo da última conversa resolvida
 * @property {number|null} [lastResolvedId] - carimbo do último contato resolvido
 */

/**
 * @typedef {Object} DeepLinkAction
 * @property {'noop'|'wait'|'select'|'open_by_id'|'deselect'} action
 * @property {'conversation'|'contact'} [via] - qual dimensão resolveu o `select`
 * @property {Record<string, any>} [row] - a linha da sidebar (só em `select`)
 * @property {number} [conversationId] - só em `open_by_id`
 * @property {number} [contactId] - só em `select` via contato
 */

/**
 * Decide o que fazer com o deep-link corrente.
 *
 * - `noop`: já resolvido (o carimbo bate) ou não há nada a fazer.
 * - `wait`: ainda não dá para decidir — o efeito fica ARMADO e re-tenta quando as
 *   deps mudarem (a lista chegar, a busca popular `contacts`, um upsert de WS…).
 * - `select`: há linha na sidebar → abre por telefone + conversa + canal dela.
 * - `open_by_id`: não há linha → abre a conversa DIRETO pelo id; o loader de detalhe
 *   é conversa-cêntrico e deriva telefone/canal da resposta do endpoint.
 * - `deselect`: voltou para `/` (popstate) tendo algo resolvido antes.
 *
 * @param {DeepLinkInput} input
 * @returns {DeepLinkAction}
 */
export function resolveDeepLink({
  initialConversationId = null,
  initialContactId = null,
  contacts = [],
  loading = false,
  lastResolvedConvId = null,
  lastResolvedId = null,
} = {}) {
  const rows = contacts || [];

  if (initialConversationId != null) {
    if (initialConversationId === lastResolvedConvId) return { action: 'noop' };
    // Espera SÓ o `loading` (plano 89 · F2). O guard antigo era
    // `rows.length === 0 || loading`, que conflaciona "a lista ainda não chegou"
    // (esperar é certo) com "a lista chegou e está VAZIA" (aí é preciso abrir mesmo
    // assim) — e o `loading` já distingue as duas. Um link de conversa é um endereço
    // permanente: abrir não pode depender de qual aba/chip o operador tinha na
    // sidebar. O `open_by_id` abaixo já sabia abrir sem linha desde `288d686`.
    if (loading) return { action: 'wait' };
    const row = rows.find(c => c.conversation_id === initialConversationId);
    if (row) {
      return { action: 'select', via: 'conversation', row, conversationId: initialConversationId };
    }
    // Atendimento fora da sidebar (além da 1ª página, arquivado, ou simplesmente
    // fora da view corrente) — abre direto por id.
    return { action: 'open_by_id', conversationId: initialConversationId };
  }

  if (initialContactId == null) {
    // popstate de volta para "/" — desmarca sem empurrar URL de novo.
    if (lastResolvedId != null || lastResolvedConvId != null) return { action: 'deselect' };
    return { action: 'noop' };
  }

  if (initialContactId === lastResolvedId) return { action: 'noop' };
  // ASSIMETRIA DELIBERADA (não "consertar por simetria"): o ramo do CONTATO espera
  // mesmo a lista ter linhas. Não existe endpoint "abrir contato por id" equivalente
  // ao de conversa — sem linha não há o que abrir, e o carimbo só é gravado no
  // sucesso, então o efeito re-tenta sozinho quando a lista chegar.
  if (rows.length === 0 || loading) return { action: 'wait' };
  const row = rows.find(c => c.contact_id === initialContactId)
    || rows.find(c => c.id === initialContactId);
  if (row) return { action: 'select', via: 'contact', row, contactId: initialContactId };
  return { action: 'wait' };
}
