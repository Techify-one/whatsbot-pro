// Run with: node --test web/static/js/services/hubDefaults.test.js
//
// Plano 88 · F1 — trava as duas regras de "qual aba o hub mostra": o default do mount
// (que inverte o significado da URL limpa) e o "a aba cede" da conversa aberta. Este
// caminho decidia o que o atendente vê ao voltar de outra tela e não tinha nenhuma
// cobertura — o default morava numa constante dentro do componente.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readParams, writeParams } from './urlState.js';
import {
  hasStoredUser, defaultAssignmentTab, buildHubUrlSchema, hubUrlHasParams,
  shouldYieldAssignmentTab,
} from './hubDefaults.js';

// ── localStorage stub ────────────────────────────────────────────────────────
// `node --test` não tem localStorage; o módulo lê `globalThis.localStorage` atrás de
// um guard, então basta plantar/remover o objeto.
function withStorage(getItem, fn) {
  const prev = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', {
    value: { getItem }, configurable: true, writable: true,
  });
  try { fn(); } finally {
    if (prev) Object.defineProperty(globalThis, 'localStorage', prev);
    else delete globalThis.localStorage;
  }
}

// ── hasStoredUser ────────────────────────────────────────────────────────────

test('hasStoredUser: usuário logado gravado pelo AuthGate', () => {
  withStorage(() => JSON.stringify({ id: 7, name: 'Luisa' }), () => {
    assert.equal(hasStoredUser(), true);
  });
});

test('hasStoredUser: sem chave, JSON quebrado ou objeto sem id → false', () => {
  withStorage(() => null, () => assert.equal(hasStoredUser(), false));
  withStorage(() => '{{{', () => assert.equal(hasStoredUser(), false));
  withStorage(() => JSON.stringify({ name: 'sem id' }), () => assert.equal(hasStoredUser(), false));
});

test('hasStoredUser: storage indisponível/lançando degrada para false (nunca lança)', () => {
  const prev = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  if (prev) delete globalThis.localStorage;
  assert.equal(hasStoredUser(), false);              // modo privado / node puro
  if (prev) Object.defineProperty(globalThis, 'localStorage', prev);

  withStorage(() => { throw new Error('SecurityError'); }, () => {
    assert.equal(hasStoredUser(), false);
  });
});

// ── defaultAssignmentTab ─────────────────────────────────────────────────────

test('defaultAssignmentTab: com identidade abre em "Minhas"', () => {
  assert.equal(defaultAssignmentTab(true), 'mine');
});

test('defaultAssignmentTab: SEM identidade degrada para "Todas" (D2)', () => {
  // A aba "Minhas" nem é renderizada sem usuário, e `assignee=me` é recusado pelo
  // servidor — o operador ficaria numa aba invisível com lista vazia e sem erro.
  assert.equal(defaultAssignmentTab(false), 'all');
});

// ── buildHubUrlSchema ────────────────────────────────────────────────────────

test('URL limpa cai no default do schema (o sintoma do plano)', () => {
  assert.equal(readParams('', buildHubUrlSchema('mine')).assignment, 'mine');
  assert.equal(readParams('', buildHubUrlSchema('all')).assignment, 'all');
});

test('a URL vence o default nos dois sentidos (plano 24 · D3)', () => {
  const mineDefault = buildHubUrlSchema('mine');
  assert.equal(readParams('?assignment=all', mineDefault).assignment, 'all');
  assert.equal(readParams('?assignment=unassigned', mineDefault).assignment, 'unassigned');
  // Link antigo com `?assignment=mine` continua exato mesmo sem identidade.
  assert.equal(readParams('?assignment=mine', buildHubUrlSchema('all')).assignment, 'mine');
});

test('a escrita inverte de forma: "mine" some da URL e "all" passa a aparecer', () => {
  const schema = buildHubUrlSchema('mine');
  const state = {
    status: 'open', assignment: 'mine', sort: 'activity', search: '',
    archived: false, tags: [], panel: '', adv: [],
  };
  assert.equal(writeParams(state, schema), '');
  assert.equal(writeParams({ ...state, assignment: 'all' }, schema), 'assignment=all');
});

test('nenhum outro campo do schema muda de default', () => {
  const s = readParams('', buildHubUrlSchema('mine'));
  assert.equal(s.status, 'open');
  assert.equal(s.sort, 'activity');
  assert.equal(s.search, '');
  assert.equal(s.archived, false);
  assert.deepEqual(s.tags, []);
  assert.equal(s.panel, '');
  assert.equal(s.adv, null);
});

// ── hubUrlHasParams ──────────────────────────────────────────────────────────

test('hubUrlHasParams: só chave do hub conta (precedência URL > preset salvo)', () => {
  assert.equal(hubUrlHasParams(''), false);
  assert.equal(hubUrlHasParams('?foo=1'), false);
  assert.equal(hubUrlHasParams('?assignment=all'), true);
  assert.equal(hubUrlHasParams('?tags=vip'), true);
  // Independe do default usado para construir o schema (as chaves são as mesmas).
  assert.equal(hubUrlHasParams('?status=closed', buildHubUrlSchema('mine')), true);
});

// ── shouldYieldAssignmentTab ─────────────────────────────────────────────────

const OPEN = { hasOpenThread: true, currentUserId: 7 };

test('a aba cede quando a conversa aberta não é minha', () => {
  assert.equal(shouldYieldAssignmentTab({
    ...OPEN, assignmentTab: 'mine', conversation: { assignee_user_id: 9 },
  }), true);
});

test('a aba NÃO cede quando a conversa aberta é minha', () => {
  assert.equal(shouldYieldAssignmentTab({
    ...OPEN, assignmentTab: 'mine', conversation: { assignee_user_id: 7 },
  }), false);
});

test('a linha da sidebar serve de evidência quando o detalhe não trouxe a conversa', () => {
  assert.equal(shouldYieldAssignmentTab({
    ...OPEN, assignmentTab: 'mine', row: { assignee_user_id: 7 },
  }), false);
  assert.equal(shouldYieldAssignmentTab({
    ...OPEN, assignmentTab: 'mine', row: { assignee_user_id: null },
  }), true);
});

test('a conversa carregada tem precedência sobre a linha (a linha pode estar velha)', () => {
  assert.equal(shouldYieldAssignmentTab({
    ...OPEN, assignmentTab: 'mine',
    conversation: { assignee_user_id: 7 }, row: { assignee_user_id: null },
  }), false);
});

test('conversa NOVA (sem linha e sem detalhe): "Minhas" cede, "Não atribuídas" não', () => {
  // Ela nasce sem responsável ⇒ nunca é minha, mas é legitimamente não atribuída.
  assert.equal(shouldYieldAssignmentTab({ ...OPEN, assignmentTab: 'mine' }), true);
  assert.equal(shouldYieldAssignmentTab({ ...OPEN, assignmentTab: 'unassigned' }), false);
});

test('"Não atribuídas" cede quando a conversa aberta já tem dono', () => {
  assert.equal(shouldYieldAssignmentTab({
    ...OPEN, assignmentTab: 'unassigned', conversation: { assignee_user_id: 9 },
  }), true);
  assert.equal(shouldYieldAssignmentTab({
    ...OPEN, assignmentTab: 'unassigned', conversation: { assignee_user_id: null, active_agent_key: 'vendas' },
  }), true);
  assert.equal(shouldYieldAssignmentTab({
    ...OPEN, assignmentTab: 'unassigned', conversation: { assignee_user_id: null, active_agent_key: null },
  }), false);
});

test('"Todas" e "Menções" nunca cedem', () => {
  assert.equal(shouldYieldAssignmentTab({
    ...OPEN, assignmentTab: 'all', conversation: { assignee_user_id: 9 },
  }), false);
  // Abrir a menção zera `has_user_mention` na linha — julgar aqui faria a aba ceder
  // a cada menção lida.
  assert.equal(shouldYieldAssignmentTab({
    ...OPEN, assignmentTab: 'mentions', conversation: { has_user_mention: false },
  }), false);
});

test('sem conversa aberta, ou sem identidade ainda, ninguém cede', () => {
  assert.equal(shouldYieldAssignmentTab({
    assignmentTab: 'mine', currentUserId: 7, conversation: { assignee_user_id: 9 },
  }), false);
  assert.equal(shouldYieldAssignmentTab({
    hasOpenThread: true, assignmentTab: 'mine', currentUserId: null,
    conversation: { assignee_user_id: 9 },
  }), false);
});

test('chamada sem argumento nenhum não lança', () => {
  assert.equal(shouldYieldAssignmentTab(), false);
});
