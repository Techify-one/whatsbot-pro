// @ts-check
//
// Rascunhos do compositor — o texto digitado sobrevive à troca de conversa, no
// molde do WhatsApp/Chatwoot: abriu /conversations/123, digitou "Oi", foi para a
// /124 atender outro cliente e voltou → o "Oi" continua no compositor, pronto
// para enviar. A sidebar mostra "Rascunho: …" no lugar da última mensagem.
//
// PESSOAL e POR-DISPOSITIVO: o mapa vive no localStorage namespaceado pelo id do
// usuário logado (`whatsbot_drafts_v1:<userId>`), então dois operadores no MESMO
// navegador nunca veem o rascunho um do outro (e o logout não vaza para quem
// entrar depois). Nada disso vai para o servidor.
//
// Módulo PURO (sem preact, sem DOM obrigatório) para rodar em `node --test`; o
// seam de re-render é o hook hooks/useDrafts.js. Escrita e notificação são
// debounced (FLUSH_DELAY_MS) para digitar não re-renderizar a lista a cada tecla
// — o compositor tem o valor imediato no próprio state.

const KEY_PREFIX = 'whatsbot_drafts_v1:';
const USER_KEY = 'whatsbot_user';        // mesma chave que o AuthGate guarda
const ANON = 'anon';                     // instalação em modo aberto / deslogado
const MAX_DRAFTS = 300;                  // cap por usuário; os mais antigos caem
const FLUSH_DELAY_MS = 400;

/** @type {string|null} */
let _userKey = null;                     // namespace atual (null = ainda não resolvido)
/** @type {Record<string, {t: string, u: number}>|null} */
let _map = null;                         // null = não carregado do storage
let _version = 0;
let _timer = null;
const _subs = new Set();
/** @type {Set<string>|null} */
let _dirty = new Set();          // chaves mexidas desde o último aviso (null = tudo)

function storage() {
  try {
    const s = globalThis.localStorage;
    return (s && typeof s.getItem === 'function') ? s : null;
  } catch (_) { return null; }           // Safari private mode / storage bloqueado
}

// Namespace inicial: o usuário que o AuthGate já guardou, para um F5 com sessão
// ativa achar os rascunhos antes de o shell montar e chamar setDraftUser().
function storedUserKey() {
  const s = storage();
  if (!s) return ANON;
  try {
    const raw = s.getItem(USER_KEY);
    const u = raw ? JSON.parse(raw) : null;
    return (u && u.id != null) ? String(u.id) : ANON;
  } catch (_) { return ANON; }
}

function storageKey() {
  if (_userKey === null) _userKey = storedUserKey();
  return KEY_PREFIX + _userKey;
}

function ensureLoaded() {
  if (_map) return _map;
  _map = {};
  const s = storage();
  if (!s) return _map;
  try {
    const raw = s.getItem(storageKey());
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === 'object') {
      for (const [k, v] of Object.entries(parsed)) {
        // Tolera lixo/formato antigo: só entra o que tem texto de verdade.
        if (v && typeof v.t === 'string' && v.t) {
          _map[k] = { t: v.t, u: typeof v.u === 'number' ? v.u : 0 };
        }
      }
    }
  } catch (_) { /* mapa corrompido → começa vazio */ }
  return _map;
}

// Avisa os assinantes. `changed` = as chaves mexidas desde o último aviso, ou
// `null` quando o mapa inteiro virou (troca de usuário, escrita de outra aba) —
// quem só se importa com certas conversas usa isso para não re-renderizar à toa.
function bump() {
  _version++;
  const changed = _dirty ? Array.from(_dirty) : null;
  _dirty = new Set();
  for (const fn of _subs) { try { fn(_version, changed); } catch (_) { /* ignore */ } }
}

function markDirty(key) {
  if (_dirty) _dirty.add(key);
}

function markAllDirty() { _dirty = null; }

function persist() {
  const s = storage();
  if (!s) return;
  const m = ensureLoaded();
  const keys = Object.keys(m);
  if (keys.length > MAX_DRAFTS) {
    // Cap: mantém os escritos mais recentemente (o rascunho de uma conversa que
    // o operador nunca mais abriu não vale um localStorage infinito). A ordem de
    // inserção do mapa É a ordem de escrita — setDraft reinsere a chave a cada
    // gravação e a carga do storage preserva a ordem persistida.
    for (const k of keys.slice(0, keys.length - MAX_DRAFTS)) delete m[k];
  }
  try {
    if (!Object.keys(m).length) s.removeItem(storageKey());
    else s.setItem(storageKey(), JSON.stringify(m));
  } catch (_) { /* quota estourada → o rascunho segue em memória nesta aba */ }
}

function schedule() {
  if (_timer != null) return;
  _timer = setTimeout(() => { _timer = null; persist(); bump(); }, FLUSH_DELAY_MS);
}

/** Grava e notifica AGORA (envio, troca de usuário, fechar a aba). */
export function flushDrafts() {
  if (_timer != null) { clearTimeout(_timer); _timer = null; }
  persist();
  bump();
}

/**
 * Identidade do rascunho, espelhando o `rowKeyFor` da sidebar: a conversa é a
 * dona do texto (dois canais do mesmo número são conversas distintas). Linha
 * ainda sem atendimento cai no telefone e migra na criação (ver migrateDraft).
 * @param {{conversationId?: any, phone?: any}} opts
 * @returns {string|null}
 */
export function draftKeyFor({ conversationId = null, phone = null } = {}) {
  if (conversationId != null) return `conv:${conversationId}`;
  if (phone) return `phone:${phone}`;
  return null;
}

/**
 * Troca o namespace (login/logout). Descarrega o mapa do usuário anterior — ele
 * fica intacto no storage e volta quando aquele usuário logar de novo.
 * @param {any} userId
 */
export function setDraftUser(userId) {
  const next = (userId != null && userId !== '') ? String(userId) : ANON;
  if (_userKey === next) return;
  if (_userKey !== null && _timer != null) { clearTimeout(_timer); _timer = null; persist(); }
  _userKey = next;
  _map = null;
  markAllDirty();
  bump();
}

/** @param {string|null} key @returns {string} */
export function getDraft(key) {
  if (!key) return '';
  const e = ensureLoaded()[key];
  return e ? e.t : '';
}

/**
 * Quando o rascunho foi escrito (epoch ms; 0 = não há). A sidebar usa isso para
 * ordenar: conversa com rascunho conta como atividade recente e sobe na lista.
 * @param {string|null} key @returns {number}
 */
export function getDraftAt(key) {
  if (!key) return 0;
  const e = ensureLoaded()[key];
  return e ? (e.u || 0) : 0;
}

/**
 * Salva (ou apaga, quando o texto fica vazio) o rascunho da conversa.
 * @param {string|null} key
 * @param {string} text
 */
export function setDraft(key, text) {
  if (!key) return;
  const m = ensureLoaded();
  const val = typeof text === 'string' ? text : '';
  const cur = m[key];
  if (!val) {
    if (!cur) return;
    delete m[key];
  } else {
    if (cur && cur.t === val) return;
    if (cur) delete m[key];             // reinsere: ordem do mapa = ordem de escrita
    m[key] = { t: val, u: Date.now() };
  }
  markDirty(key);
  schedule();
}

/** Apaga o rascunho e notifica na hora (envio: o "Rascunho:" some da lista já). */
export function clearDraft(key) {
  if (!key) return;
  const m = ensureLoaded();
  if (!(key in m)) return;
  delete m[key];
  markDirty(key);
  flushDrafts();
}

/**
 * Move o rascunho de uma chave para outra, sem sobrescrever destino que já
 * tenha texto. Usado quando a conversa nasce: a linha começa como `phone:<n>` e
 * ganha `conv:<id>` depois do 1º envio — sem isso o texto seria descartado.
 * @param {string|null} from @param {string|null} to
 */
export function migrateDraft(from, to) {
  if (!from || !to || from === to) return;
  const m = ensureLoaded();
  const src = m[from];
  if (!src) return;
  delete m[from];
  if (!m[to]) m[to] = src;
  markDirty(from);
  markDirty(to);
  schedule();
}

/**
 * Assina mudanças; devolve a função de unsubscribe (molde do providerCatalog).
 * O callback recebe `(version, changedKeys | null)` — `null` = o mapa inteiro virou.
 */
export function subscribe(fn) { _subs.add(fn); return () => _subs.delete(fn); }
export function getDraftsVersion() { return _version; }

// Uma aba que digita não pode deixar a outra com o rascunho velho; e o texto
// digitado nos últimos FLUSH_DELAY_MS não pode morrer ao fechar a aba.
try {
  if (globalThis.addEventListener) {
    globalThis.addEventListener('storage', (e) => {
      if (e && e.key === storageKey()) { _map = null; markAllDirty(); bump(); }
    });
    globalThis.addEventListener('beforeunload', () => { if (_timer != null) flushDrafts(); });
  }
} catch (_) { /* ambiente sem DOM (node --test) */ }

/** Só para os testes: zera o estado de módulo entre casos. */
export function __resetForTests() {
  if (_timer != null) { clearTimeout(_timer); _timer = null; }
  _userKey = null;
  _map = null;
  _version = 0;
  _dirty = new Set();
  _subs.clear();
}
