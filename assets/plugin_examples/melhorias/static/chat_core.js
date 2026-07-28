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
    default:
      return { items: next, status };
  }
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

// Persistido (DB do gateway) → cards, na hidratação ao abrir o detalhe.
export function persistedToItems(messages = [], approvals = []) {
  const items = [];
  for (const m of messages) {
    if ((m.role === 'user' || m.role === 'assistant') && (m.content || '').trim()) {
      items.push({ kind: 'text', id: `db-${m.id}`, role: m.role,
                   content: m.content, streaming: false });
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
