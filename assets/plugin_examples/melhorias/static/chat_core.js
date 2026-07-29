// Núcleo PURO do chat agêntico (plano 51 · 04 F4) — sem imports de UI, testável
// com `node --test` (chat_core.test.js). Consumido por chat.js.
//
// reduceAiEvent: (items, {event, data}, status) -> {items, status}
// items: [{kind:'text', id, role, content, streaming}
//         |{kind:'tool', id, name, input, output, error, status}
//         |{kind:'approval', id, toolName, toolInput, summary, decided}
//         |{kind:'error', id, message}]
// status ∈ idle|streaming|awaiting-approval|error.

export function reduceAiEvent(items, ev, status = 'streaming') {
  const { event, data = {} } = ev || {};
  const next = items.slice();
  const findIdx = (kind, id) => next.findIndex((c) => c.kind === kind && c.id === id);

  switch (event) {
    case 'conversation_started':
      return { items: next, status };
    case 'message_start': {
      if (findIdx('text', data.messageId) === -1) {
        next.push({ kind: 'text', id: data.messageId, role: 'assistant',
                    content: '', streaming: true });
      }
      return { items: next, status: 'streaming' };
    }
    case 'message_chunk': {
      let i = findIdx('text', data.messageId);
      if (i === -1) {
        next.push({ kind: 'text', id: data.messageId, role: 'assistant',
                    content: '', streaming: true });
        i = next.length - 1;
      }
      next[i] = { ...next[i], content: (next[i].content || '') + (data.delta || '') };
      return { items: next, status: 'streaming' };
    }
    case 'message_end': {
      const i = findIdx('text', data.messageId);
      if (i !== -1) next[i] = { ...next[i], streaming: false };
      return { items: next, status };
    }
    case 'tool_call_start': {
      if (findIdx('tool', data.toolCallId) === -1) {
        next.push({ kind: 'tool', id: data.toolCallId, name: data.name,
                    input: data.input, status: 'running' });
      }
      return { items: next, status: 'streaming' };
    }
    case 'tool_call_end': {
      const i = findIdx('tool', data.toolCallId);
      if (i !== -1) {
        next[i] = { ...next[i], output: data.output, error: data.error,
                    status: data.error ? 'error' : 'done' };
      }
      return { items: next, status };
    }
    case 'approval_needed': {
      if (findIdx('approval', data.approvalId) === -1) {
        next.push({ kind: 'approval', id: data.approvalId,
                    toolName: data.toolName, toolInput: data.toolInput,
                    summary: data.summary || '', decided: null });
      }
      return { items: next, status: 'awaiting-approval' };
    }
    case 'approval_registered': {
      // write-through do gateway (payload snake_case) — mesmo card, dedupe por id.
      const id = data.id || data.approval_id;
      if (id && findIdx('approval', id) === -1) {
        next.push({ kind: 'approval', id,
                    toolName: data.tool_name || data.toolName,
                    toolInput: data.tool_input || data.toolInput,
                    summary: data.summary || '', decided: null });
      }
      return { items: next, status: 'awaiting-approval' };
    }
    case 'done':
      return { items: next, status: 'idle' };
    case 'error': {
      next.push({ kind: 'error', id: `err-${next.length}`,
                  message: data.message || 'Erro no executor.' });
      return { items: next, status: 'error' };
    }
    case 'executor_failure': {
      // Falha TIPADA (plano 61). Precisa passar pelo reducer, e não só pelo
      // rodapé: o fallback de quiescência do chat.js só desarma o "IA
      // pensando…" quando a última linha é uma resposta assistant assentada —
      // um cartão de erro nunca o arma, e o spinner giraria para sempre.
      // `idle` é o valor honesto num transitório: a conversa segue utilizável.
      const kind = data.kind || 'unknown';
      next.push({ kind: 'error', id: `fail-${kind}-${next.length}`,
                  failureKind: kind, retryAfter: data.retry_after ?? null,
                  message: data.message || 'Erro no executor.' });
      return { items: next, status: kind === 'auth_required' ? 'error' : 'idle' };
    }
    default:
      return { items: next, status };
  }
}

// Catálogo espelhado de chat_logic.EXECUTOR_KINDS. O frontend ramifica por AÇÃO,
// não por nome de kind — um kind novo do executor degrada para um cartão
// genérico sem botão em vez de exigir atualização do cliente.
export const FAILURE_ACTIONS = {
  auth_required: 'relogin',
  rate_limited: 'wait',
  overloaded: 'wait',
  quota_exceeded: 'billing',
  unknown: 'none',
};

export function failureAction(kind) {
  return FAILURE_ACTIONS[kind] || 'none';
}

// Heurística de sessão Claude expirada (porta de use-ai-chat.ts:352-360) — roda
// no evento `error` E no texto final de cada mensagem (o SDK às vezes devolve o
// erro de auth como conteúdo assistant).
export function isAuthError(text) {
  const t = String(text || '').toLowerCase();
  return /\b401\b/.test(t) || t.includes('authentication_error')
    || t.includes('invalid authentication credentials')
    || t.includes('please run /login')
    || t.includes('invalid api key');
}

// Qual foi a última falha do executor no histórico? (plano 61 — sucessor do
// `authErrorInHistory`). Resolve o "morreu enquanto ninguém olhava" E para de
// adivinhar pelo texto: sem isto, uma linha de limite de uso que por acaso cite
// um 401 abriria o modal de relogin — e queimaria o guard `authNotifiedRef`
// daquela conversa de forma PERMANENTE, deixando uma expiração real futura sem
// aviso nenhum.
export function lastFailureFromHistory(messages = []) {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i] || {};
    const content = (m.content || '').trim();
    if (!content) continue;              // tool call / linha vazia não decide nada
    if (m.role === 'system') {
      // Caminho normal: o tipo veio gravado, sem chute.
      if (m.failure_kind) return { kind: m.failure_kind, message: content };
      // Linha legada (gravada antes do kind tipado existir): sobrou a heurística.
      return isAuthError(content)
        ? { kind: 'auth_required', message: content, legacy: true } : null;
    }
    if (m.role === 'assistant') {
      // Uma resposta da IA NUNCA é lida como falha, nem que cite um "401".
      // Uma linha `system` existe PORQUE o gateway classificou uma falha —
      // olhar o texto dela é razoável. Uma linha `assistant` é conteúdo da IA:
      // tratar o texto como sinal é exatamente o palpite que este plano remove
      // (uma análise que mencione um erro 401 abriria o relogin a cada F5).
      // O 401 legado gravado como `assistant` já é neutralizado no servidor —
      // `_last_assistant_content` o pula e ele nunca vira a análise final.
      return null;
    }
    // `user`: o operador ter escrito depois da falha não prova nada sobre o
    // executor — segue procurando para trás.
  }
  return null;
}

// Shim do nome antigo (o painel e testes externos podem usá-lo).
export function authErrorInHistory(messages = []) {
  return (lastFailureFromHistory(messages) || {}).kind === 'auth_required';
}

// Falhas cujo registro no histórico continua valendo depois de um F5. Um 429
// passa em segundos (mostrar de novo seria mentira); falta de crédito e sessão
// expirada não passam sozinhas.
const DURABLE_FAILURES = ['auth_required', 'quota_exceeded'];

// Qual rodapé o chat deve renderizar. Puro de propósito: é aqui que mora a
// regra de "quem ganha botão de Renovar sessão", e ela é testável sem DOM.
export function footerStateFor({ convStatus = 'ACTIVE', historyFailure = null,
                                 liveFailure = null } = {}) {
  const asFooter = (f) => {
    const action = failureAction(f.kind);
    if (action === 'relogin') return { mode: 'auth_expired', kind: f.kind, canRenew: true };
    if (action === 'wait') {
      return { mode: 'wait', kind: f.kind, canRenew: false,
               retryAfter: f.retryAfter ?? null };
    }
    if (action === 'billing') return { mode: 'quota', kind: f.kind, canRenew: false };
    return { mode: 'generic', kind: f.kind, canRenew: false, message: f.message || '' };
  };

  // O status é o sinal autoritativo: é o que cobre o caso em que a linha de auth
  // não é mais a última do histórico. Parece redundante com a checagem de
  // histórico abaixo, e não é — não "simplifique" removendo.
  if (convStatus === 'AUTH_EXPIRED') {
    return { mode: 'auth_expired', kind: 'auth_required', canRenew: true };
  }
  if (liveFailure && liveFailure.kind) return asFooter(liveFailure);
  if (historyFailure && DURABLE_FAILURES.includes(historyFailure.kind)) {
    return asFooter(historyFailure);
  }
  return { mode: convStatus === 'ACTIVE' ? 'live' : 'ended', kind: null, canRenew: false };
}

// Persistido (DB do gateway) → cards, na hidratação ao abrir o detalhe.
export function persistedToItems(messages = [], approvals = []) {
  const items = [];
  for (const m of messages) {
    if ((m.role === 'user' || m.role === 'assistant') && (m.content || '').trim()) {
      items.push({ kind: 'text', id: `db-${m.id}`, role: m.role,
                   content: m.content, streaming: false });
    } else if (m.role === 'system' && (m.content || '').trim()) {
      // O gateway grava a falha do executor como `system` (plano 60 · 2.3) —
      // antes `system` era descartado em silêncio e o histórico ficava com um
      // buraco. `failure_kind` (plano 61) diz de QUAL falha se trata.
      items.push({ kind: 'error', id: `db-${m.id}`, message: m.content,
                   failureKind: m.failure_kind || null });
    } else if (m.role === 'tool' && m.tool_name) {
      items.push({ kind: 'tool', id: `db-tool-${m.id}`, name: m.tool_name,
                   input: m.tool_input, output: m.tool_result, status: 'done' });
    }
  }
  for (const a of approvals) {
    items.push({ kind: 'approval', id: a.id, toolName: a.tool_name,
                 toolInput: a.tool_input, summary: a.summary || '',
                 decided: a.approved == null ? null : !!a.approved });
  }
  return items;
}
