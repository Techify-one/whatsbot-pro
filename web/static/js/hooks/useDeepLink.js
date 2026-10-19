// Deep-link de entidade por URL (DRY) — uma URL com o identificador natural da
// entidade aberta em cada tela, espelhando o que /contacts/<id> já faz.
//
// Centraliza num só lugar (1) o registry de rotas de entidade, (2) os helpers
// de parse/build de URL e (3) o hook `useDeepLink`, em vez de copiar a lógica de
// pushState/popstate por ~10 componentes. O app.js usa `entityFromPath()` para
// resolver a tab+seleção a partir da URL; cada tela usa `useDeepLink` para
// reabrir a entidade no load/back-forward e empurrar a URL ao selecionar.
//
// Modelo de seleção: `{ sub?, id? }`. `sub` é a sub-aba (agents/prompts/… no
// /ai, roles e teams em /users, o escopo contact/conversation em custom-attributes);
// `id` é o identificador natural da entidade (agent_key, plugin id, short_code…).
import { useRef, useEffect, useCallback } from 'preact/hooks';

const enc = encodeURIComponent;
const dec = decodeURIComponent;

// Sub-abas conhecidas (segmento fixo, NÃO um id de entidade).
const AI_SUBS = new Set(['agents', 'prompts', 'variables', 'tools', 'general']);
const CA_SCOPES = new Set(['contact', 'conversation']);

// Uma entrada por tab deep-linkável. `parse(rest)` recebe os segmentos da URL
// depois da base e devolve `{ sub?, id? }` (ou null se não casar); `build(sel)`
// monta o path. Contatos/atendimentos/execuções continuam no app.js (já existiam).
const ENTITY_ROUTES = [
  {
    // Tela "Contatos" (lista full-page): /contacts/{id} abre o detalhe do contato.
    // (/contacts sem id é a lista — resolvido por CORE_ROUTES, não aqui.) O hub de
    // atendimentos usa /conversations/{id}; este id é o contact_id.
    tab: 'contatos', base: '/contacts',
    parse: (r) => (r.length === 1 && /^\d+$/.test(r[0])) ? { id: parseInt(r[0], 10) } : null,
    build: ({ id }) => `/contacts/${id}`,
  },
  {
    tab: 'ai', base: '/ai',
    // /ai/{sub}  ou  /ai/{sub}/{id}
    parse: (r) => (r.length && AI_SUBS.has(r[0]))
      ? { sub: r[0], id: r[1] != null ? dec(r[1]) : undefined } : null,
    build: ({ sub, id }) => `/ai/${enc(sub)}${id != null ? `/${enc(id)}` : ''}`,
  },
  {
    tab: 'plugins', base: '/plugins',
    parse: (r) => r.length === 1 ? { id: dec(r[0]) } : null,
    build: ({ id }) => `/plugins/${enc(id)}`,
  },
  {
    tab: 'channels', base: '/channels',
    parse: (r) => r.length === 1 ? { id: dec(r[0]) } : null,
    build: ({ id }) => `/channels/${enc(id)}`,
  },
  {
    tab: 'users', base: '/users',
    // /users/{id}  ·  /users/roles[/{role_key}]  ·  /users/teams[/{team_id}]
    parse: (r) => {
      if (r[0] === 'roles') return { sub: 'roles', id: r[1] != null ? dec(r[1]) : undefined };
      if (r[0] === 'teams') return { sub: 'teams', id: r[1] != null ? dec(r[1]) : undefined };
      return r.length === 1 ? { id: dec(r[0]) } : null;
    },
    build: ({ sub, id }) => sub === 'roles'
      ? `/users/roles${id != null ? `/${enc(id)}` : ''}`
      : sub === 'teams'
        ? `/users/teams${id != null ? `/${enc(id)}` : ''}`
        : `/users/${enc(id)}`,
  },
  {
    tab: 'quick-replies', base: '/quick-replies',
    parse: (r) => r.length === 1 ? { id: dec(r[0]) } : null,
    build: ({ id }) => `/quick-replies/${enc(id)}`,
  },
  {
    tab: 'custom-attributes', base: '/custom-attributes',
    // /custom-attributes/{scope}  ou  /custom-attributes/{scope}/{key}  (sub = escopo)
    parse: (r) => (r.length && CA_SCOPES.has(r[0]))
      ? { sub: r[0], id: r[1] != null ? dec(r[1]) : undefined } : null,
    build: ({ sub, id }) => `/custom-attributes/${enc(sub)}${id != null ? `/${enc(id)}` : ''}`,
  },
];

const entryForTab = (tab) => ENTITY_ROUTES.find((r) => r.tab === tab);

// Resolve a seleção de entidade a partir da URL atual. Devolve `{ tab, sub?, id? }`
// ou null (URL sem entidade — ex.: bare /ai, /plugins, /, ou rota não-entidade).
export function entityFromPath() {
  const segs = window.location.pathname.split('/').filter(Boolean);
  if (!segs.length) return null;
  // Times voltou a ser sub-aba de Usuários (/users/teams). O caminho próprio
  // /teams[/{id}] existiu por um tempo e pode estar em favoritos: resolve para a
  // mesma sub-aba. Isto roda ANTES do primeiro render (a aba inicial vem daqui),
  // e o TeamsManager reescreve a URL para /users/teams só depois do gate.
  if (segs[0] === 'teams' && segs.length <= 2) {
    return { tab: 'users', sub: 'teams', id: segs[1] != null ? dec(segs[1]) : undefined, legacy: true };
  }
  const entry = ENTITY_ROUTES.find((r) => r.base === `/${segs[0]}`);
  if (!entry) return null;
  const rest = segs.slice(1);
  if (!rest.length) return null; // bare base → tab list, não é entidade
  const sel = entry.parse(rest);
  return sel ? { tab: entry.tab, sub: sel.sub, id: sel.id } : null;
}

// Monta o path de uma seleção (`{ sub?, id? }`) para uma tab.
export function entityPath(tab, sel) {
  const entry = entryForTab(tab);
  return entry ? entry.build(sel) : null;
}

// Path "base" (lista, sem entidade) de uma tab.
export function basePath(tab) {
  const entry = entryForTab(tab);
  return entry ? entry.base : '/';
}

// Chave canônica de uma seleção, para comparar URL-atual vs estado-da-tela.
function selKey(sel) {
  if (!sel || (sel.id == null && sel.sub == null)) return '';
  return `${sel.sub || ''}\0${sel.id == null ? '' : sel.id}`;
}

// Hook de deep-link de UMA tela.
//   tab     – id da tab (deve casar com um ENTITY_ROUTES).
//   resolve – seleção (`{ sub?, id? }` | null) vinda da URL, JÁ filtrada como
//             relevante para esta tela (o pai passa null se não for sua entidade).
//   ready   – a lista da tela já carregou? (gate igual ao Contacts.js — senão o
//             deep-link dispara antes dos dados e vira no-op).
//   open    – `(sel|null) => void`: aplica a seleção (sub-aba + entidade) ou limpa.
// Retorna `push(sel|null)`: reflete a seleção na URL ao abrir/fechar/trocar.
export function useDeepLink({ tab, resolve, ready = true, open }) {
  const appliedRef = useRef(undefined);
  const openRef = useRef(open);
  openRef.current = open; // sempre o open mais recente (evita closure stale)
  const k = selKey(resolve);

  // Resolve a partir da URL no mount, no popstate (back/forward) e quando a lista
  // termina de carregar. NÃO dispara popstate sintético — só o setTab de troca de
  // tab faz isso; aqui causaria loop de resolução.
  useEffect(() => {
    if (!ready) return;
    if (k === appliedRef.current) return;
    appliedRef.current = k;
    openRef.current(resolve || null);
  }, [k, ready]);

  return useCallback((sel) => {
    const norm = (sel && (sel.id != null || sel.sub != null)) ? sel : null;
    appliedRef.current = selKey(norm); // mantém em sync com aberturas manuais
    const path = norm ? entityPath(tab, norm) : basePath(tab);
    if (path && window.location.pathname !== path) {
      history.pushState(null, '', path);
    }
  }, [tab]);
}
