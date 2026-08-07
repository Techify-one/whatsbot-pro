// @ts-check
//
// Qual aba de atribuição o hub de conversas mostra — plano 88.
//
// Duas regras moram aqui, as duas respondendo "qual aba o operador vê":
//
//  1. DEFAULT DO MOUNT (`defaultAssignmentTab` + `buildHubUrlSchema`) — trocar de tela
//     no app DESMONTA o <Contacts/> (shell/ScreenRouter.js:129) e voltar é um
//     `pushState('/')` SEM query-string (shell/App.js:167), então o hub renasce nos
//     defaults do schema de URL. Com o default 'all' o atendente caía em "Todas" toda
//     vez que ia em Protocolos/Contatos/Configurações e voltava — o sintoma que abriu
//     o plano. O default resolvido aqui é 'mine'.
//
//  2. A ABA CEDE (`shouldYieldAssignmentTab`) — a conversa ABERTA que a aba corrente
//     não consegue mostrar faz a aba cair para "Todas". Uma regra, dois consumidores:
//     a conversa que o operador acabou de iniciar (nasce sem responsável) e o
//     deep-link de uma conversa que não é dele (plano 89 · P1).
//
// SEM IDENTIDADE O DEFAULT DEGRADA para 'all', e isso não é zelo defensivo: (a) a aba
// "Minhas" nem chega a ser renderizada sem usuário (ConversationFilterBar.js:478) — o
// operador ficaria numa aba invisível; (b) o servidor RECUSA `assignee=me` sem sessão
// (db/filters/translate.py:220) e a sidebar responderia lista vazia em silêncio
// (hooks/useConversationList.js:232).
//
// O default do schema é TAMBÉM a regra de omissão na escrita (services/urlState.js:50),
// então a URL inverte de forma: `/` passa a significar "Minhas" e escolher "Todas"
// escreve `?assignment=all`. É intencional — um link COM `?assignment=…` continua
// exato nos dois valores, porque a URL vence o default (plano 24 · D3).
//
// A aba "Minhas" é filtro de EXIBIÇÃO, nunca de acesso: o servidor não escopa conversa
// por dono (server/authz.py:77-90 — o único escopo real é membership de canal), e o
// plano 89 congelou esse contrato em teste. Nada aqui restringe o que pode ser aberto.
//
// PURO: sem preact, sem DOM (o localStorage é lido atrás de um guard), sem rede.
import { enumStr, str, bool, list, json } from './urlState.js';
import { matchesAssignment } from './conversationRows.js';

const USER_KEY = 'whatsbot_user';   // a mesma chave que o AuthGate grava (e drafts.js lê)

/**
 * Há usuário logado? Fonte SÍNCRONA de identidade, para decidir o default no mount sem
 * esperar o `getMe()` (que só popula `currentUserId` depois de uma ida à rede — e o
 * schema de URL precisa do valor no primeiro render). Mesmo truque do drafts.js:19.
 * Storage indisponível (modo privado, política do navegador) devolve `false` → o hub
 * abre em "Todas". Pior UX que "Minhas", nunca uma tela branca.
 * @returns {boolean}
 */
export function hasStoredUser() {
  try {
    const s = globalThis.localStorage;
    if (!s || typeof s.getItem !== 'function') return false;
    const raw = s.getItem(USER_KEY);
    if (!raw) return false;
    const u = JSON.parse(raw);
    return !!(u && u.id != null);
  } catch (_) {
    return false;
  }
}

/**
 * A aba que o hub abre quando a URL não diz nada.
 * @param {boolean} hasIdentity - há usuário logado (ver `hasStoredUser`).
 * @returns {'mine'|'all'}
 */
export function defaultAssignmentTab(hasIdentity) {
  return hasIdentity ? 'mine' : 'all';
}

/**
 * O schema de deep-link do hub (plano 24). Era uma constante de módulo; virou fábrica
 * só porque o default de `assignment` passou a depender da identidade — nenhum outro
 * campo muda.
 * @param {string} [defaultAssignment] - 'all' | 'mine' | 'unassigned' | 'mentions'
 * @returns {import('./urlState.js').Field[]}
 */
export function buildHubUrlSchema(defaultAssignment = 'all') {
  return [
    enumStr('status', 'open'),                 // open|closed|all
    enumStr('assignment', defaultAssignment),  // all|mine|unassigned|mentions
    enumStr('sort', 'activity'),               // activity|oldest|unread
    str('search', ''),
    bool('archived'),
    list('tags'),
    str('panel', ''),                          // ''|contact|conversation
    json('adv', { isDefault: (v) => !Array.isArray(v) || v.length === 0 }),
  ];
}

/**
 * A URL traz algum filtro do hub? (decide a precedência URL > preset salvo — plano
 * 24 · D3). As chaves não dependem do default, então o schema é opcional.
 * @param {string} search - `location.search`, com ou sem o '?'
 * @param {import('./urlState.js').Field[]} [schema]
 * @returns {boolean}
 */
export function hubUrlHasParams(search, schema = buildHubUrlSchema()) {
  const p = new URLSearchParams(search || '');
  return schema.some((f) => p.has(f.key));
}

/**
 * A aba de atribuição deve CEDER para "Todas" por causa da conversa que acabou de ser
 * aberta? A aba é uma VIEW, não um filtro salvo (ela é excluída de propósito do spec
 * de preset — useConversationFilters.js:239-244), então trocá-la não descarta nada do
 * operador; deixar a conversa aberta sem linha na sidebar, sim, esconde contexto.
 *
 * Só 'mine' e 'unassigned' cedem:
 *  • 'all' já mostra tudo.
 *  • 'mentions' é PER-USUÁRIO e some ao ser lida — abrir a menção zera
 *    `has_user_mention` na linha (useConversationSelection.js:266), então julgar aqui
 *    faria a aba ceder a CADA menção aberta. A reconciliação dela é outra (plano 72 F8).
 *
 * A evidência preferida é a conversa CARREGADA (`conversation`, fresca e autoritativa);
 * a linha da sidebar (`row`) serve de reserva. SEM evidência nenhuma só 'mine' cede:
 * uma thread sem linha e sem conversa carregada é uma conversa NOVA, que nasce sem
 * responsável (o único carimbo automático é o `default_assignee_user_id` do inbound —
 * channels/ai_settings.py) ⇒ nunca é "minha", mas é legitimamente "não atribuída".
 *
 * @param {Object} input
 * @param {string} [input.assignmentTab]
 * @param {number|null} [input.currentUserId]
 * @param {Record<string, any>|null} [input.conversation] - detalhe carregado da conversa aberta
 * @param {Record<string, any>|null} [input.row] - linha da sidebar da conversa aberta
 * @param {boolean} [input.hasOpenThread] - há uma conversa aberta no painel
 * @returns {boolean}
 */
export function shouldYieldAssignmentTab({
  assignmentTab = 'all',
  currentUserId = null,
  conversation = null,
  row = null,
  hasOpenThread = false,
} = {}) {
  if (!hasOpenThread) return false;
  if (assignmentTab !== 'mine' && assignmentTab !== 'unassigned') return false;
  // Identidade ainda não chegou (o `getMe()` é assíncrono): não dá para julgar "minha",
  // e ceder aqui viraria uma troca de aba no meio do carregamento da sessão.
  if (assignmentTab === 'mine' && currentUserId == null) return false;
  const evidence = conversation || row;
  if (!evidence) return assignmentTab === 'mine';
  return !matchesAssignment(evidence, assignmentTab, currentUserId);
}
