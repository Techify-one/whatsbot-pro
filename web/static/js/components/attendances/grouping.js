// Abstração de agrupamento do kanban de Atendimentos (lógica pura, sem JSX).
//
// Cada modo é descrito por um objeto uniforme:
//   { id, label, requiredPerm, columns(), columnIdOf(conv), onDrop(conv, colId) }
// O board consome SÓ essa interface — trocar de modo é trocar o objeto, sem if
// espalhado pela UI. `ctx.actions` injeta as chamadas de API (ver Attendances.js).

export const UNASSIGNED = '__unassigned__';
export const NO_STAGE = '__no_stage__';
export const NO_LABEL = '__no_label__';

// Modos disponíveis (ordem do seletor). 'stage' só aparece se houver ≥1 atributo lista.
export const GROUP_MODES = [
  { id: 'assignee', label: 'Por atendente' },
  { id: 'stage', label: 'Por etapa' },
  { id: 'label', label: 'Por etiqueta' },
  { id: 'status', label: 'Por status' },
];

export function buildGrouping(mode, ctx) {
  const { agents = { users: [], ai_agents: [] }, labels = [], attributeDef = null, actions = {} } = ctx || {};

  switch (mode) {
    case 'assignee':
      return {
        id: 'assignee',
        label: 'Por atendente',
        requiredPerm: 'conversation.assign',
        columns: () => [
          { id: UNASSIGNED, label: 'Não atribuídos' },
          ...(agents.users || []).map(u => ({ id: `u:${u.id}`, label: u.name || `Usuário #${u.id}` })),
          ...(agents.ai_agents || []).map(a => ({ id: `a:${a.agent_key}`, label: a.display_name || a.agent_key, isAi: true })),
        ],
        columnIdOf: (c) =>
          c.assignee_user_id != null ? `u:${c.assignee_user_id}`
            : c.active_agent_key ? `a:${c.active_agent_key}`
              : UNASSIGNED,
        // Patch otimista aplicado pelo container; aqui só a chamada de API.
        patchFor: (colId) =>
          colId === UNASSIGNED ? { assignee_user_id: null, active_agent_key: null, ai_active: 0 }
            : colId.startsWith('u:') ? { assignee_user_id: +colId.slice(2), active_agent_key: null, ai_active: 0 }
              : { assignee_user_id: null, active_agent_key: colId.slice(2), ai_active: 1 },
        onDrop: (c, colId) =>
          colId === UNASSIGNED ? actions.assignAgent(c.id, { kind: 'none' })
            : colId.startsWith('u:') ? actions.assignAgent(c.id, { kind: 'user', userId: +colId.slice(2) })
              : actions.assignAgent(c.id, { kind: 'ai', agentKey: colId.slice(2) }),
      };

    case 'stage':
      return {
        id: 'stage',
        label: 'Por etapa',
        requiredPerm: 'conversation.reply',
        columns: () => [
          { id: NO_STAGE, label: 'Sem etapa' },
          ...((attributeDef && attributeDef.options) || []).map(o => ({ id: `o:${o}`, label: o })),
        ],
        columnIdOf: (c) => {
          const key = attributeDef && attributeDef.attribute_key;
          const v = key ? (c.custom_attributes || {})[key] : null;
          return v ? `o:${v}` : NO_STAGE;
        },
        patchFor: (colId) => {
          const key = attributeDef && attributeDef.attribute_key;
          const v = colId === NO_STAGE ? null : colId.slice(2);
          return { custom_attributes_patch: key ? { [key]: v } : {} };
        },
        onDrop: (c, colId) => {
          const key = attributeDef && attributeDef.attribute_key;
          const v = colId === NO_STAGE ? null : colId.slice(2);
          return actions.setStage(c, key, v);
        },
      };

    case 'label':
      return {
        id: 'label',
        label: 'Por etiqueta',
        requiredPerm: 'conversation.reply',
        columns: () => [
          { id: NO_LABEL, label: 'Sem etiqueta' },
          ...labels.map(l => ({ id: `l:${l.name}`, label: l.name, color: l.color })),
        ],
        // 1ª etiqueta-que-é-coluna presente decide a coluna (evita o card aparecer em N).
        columnIdOf: (c) => {
          const set = new Set(c.label_names || []);
          const hit = labels.find(l => set.has(l.name));
          return hit ? `l:${hit.name}` : NO_LABEL;
        },
        // Mover = trocar a etiqueta-coluna: remove todas as labels que são coluna do
        // board e adiciona só a alvo; etiquetas avulsas (não-coluna) são preservadas.
        patchFor: (colId, c) => {
          const colNames = new Set(labels.map(l => l.name));
          const kept = (c.label_names || []).filter(n => !colNames.has(n));
          const next = colId === NO_LABEL ? kept : [...kept, colId.slice(2)];
          return { label_names: next };
        },
        onDrop: (c, colId) => {
          const colNames = new Set(labels.map(l => l.name));
          const kept = (c.label_names || []).filter(n => !colNames.has(n));
          const next = colId === NO_LABEL ? kept : [...kept, colId.slice(2)];
          return actions.replaceLabels(c.id, next);
        },
      };

    case 'status':
    default:
      return {
        id: 'status',
        label: 'Por status',
        requiredPerm: 'conversation.resolve',
        columns: () => [
          { id: 'open', label: 'Abertas' },
          { id: 'closed', label: 'Fechadas' },
        ],
        columnIdOf: (c) => (c.status === 'closed' ? 'closed' : 'open'),
        patchFor: (colId) => ({ status: colId }),
        // Pass the full conversation so resolveConversation can run beforeResolve.
        onDrop: (c, colId) => actions.setStatus(c, colId),
      };
  }
}
