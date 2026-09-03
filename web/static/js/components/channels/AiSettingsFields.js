// Channels — AiSettingsFields (Plano 23 · D4), extracted verbatim from
// ChannelsManager.js. Per-channel AI settings form (config.ai). Controlled:
// `value` is the ai object, `onChange` receives the full updated object. Mirrors
// the knobs that used to be global in the IA "Configurações" tab — now per channel.
import { h } from 'preact';
import htm from 'htm';
import { mediaModesFrom, serializeMediaModes } from './constants.js';
import { SearchableSelect } from '../SearchableSelect.js';

const html = htm.bind(h);

// Prefixos do valor do seletor de "atendente padrão". O campo guarda DUAS chaves
// distintas na config (`default_assignee_user_id`, int; `default_assignee_agent_key`,
// texto) e o <select> precisa de um espaço de valores único — daí `u:` e `ia:`.
// `ia:` é o MESMO encoding que o plugin fechamento_ia usa para o rótulo do tipo
// `atendente`; manter os dois iguais evita duas gramáticas para a mesma ideia.
const ASSIGNEE_USER_PREFIX = 'u:';
const ASSIGNEE_AI_PREFIX = 'ia:';

export function AiSettingsFields({ value, onChange, sequentialDefault = true, users = [],
                                  aiAgents = [] }) {
  const ai = value || {};
  const set = (key, v) => onChange({ ...ai, [key]: v });
  const num = (key, v, fallback) => {
    const n = parseFloat(v);
    set(key, isNaN(n) ? fallback : n);
  };
  const aiOn = ai.ai_enabled !== false;
  // Default attendant for NEW conversations (plano 71, estendido no plano 152):
  // um HUMANO (`default_assignee_user_id`, int) OU um AGENTE DE IA
  // (`default_assignee_agent_key`, texto). São MUTUAMENTE EXCLUSIVOS — escolher um
  // zera o outro no mesmo onChange, para nunca existir config com os dois (que o
  // backend resolveria a favor do humano, não do que o operador acabou de clicar).
  const assigneeAgentKey = ai.default_assignee_agent_key || '';
  const assigneeVal = ai.default_assignee_user_id != null
    ? ASSIGNEE_USER_PREFIX + String(ai.default_assignee_user_id)
    : (assigneeAgentKey ? ASSIGNEE_AI_PREFIX + assigneeAgentKey : '');
  const pickAssignee = (v) => {
    if (!v) return onChange({ ...ai, default_assignee_user_id: null, default_assignee_agent_key: null });
    if (v.startsWith(ASSIGNEE_AI_PREFIX)) {
      return onChange({ ...ai, default_assignee_user_id: null,
                        default_assignee_agent_key: v.slice(ASSIGNEE_AI_PREFIX.length) });
    }
    const uid = parseInt(v.slice(ASSIGNEE_USER_PREFIX.length), 10);
    return onChange({ ...ai, default_assignee_user_id: isNaN(uid) ? null : uid,
                      default_assignee_agent_key: null });
  };
  // Com o master do canal DESLIGADO os agentes de IA não são oferecidos: uma IA
  // que o gate do canal cala nunca assumiria a conversa, e anunciá-la no seletor
  // seria a mesma promessa vazia que o fix atribuição-IA-off (2026-07) tirou do
  // nascimento. O que já está SALVO continua listado (senão o campo mostraria a
  // string crua "ia:<chave>" em vez do nome) — com o aviso logo abaixo.
  const offeredAiAgents = aiOn
    ? aiAgents
    : aiAgents.filter((a) => a.agent_key === assigneeAgentKey);
  const assigneeOptions = [
    ...users.map((u) => ({ value: ASSIGNEE_USER_PREFIX + String(u.id),
                           label: u.name || u.email, sublabel: u.name ? u.email : '' })),
    ...offeredAiAgents.map((a) => ({ value: ASSIGNEE_AI_PREFIX + a.agent_key,
                                     label: a.display_name || a.agent_key,
                                     sublabel: 'Agente de IA' })),
  ];
  // Agente salvo que sumiu do catálogo (excluído ou desativado em /ai/agents):
  // entra como opção "órfã" só para o campo mostrar a chave em vez de ficar em
  // branco. O backend já o ignora (agent_repo.get → enabled), então o aviso abaixo
  // é o que conta.
  const assigneeAgentKnown = !assigneeAgentKey
    || aiAgents.some((a) => a.agent_key === assigneeAgentKey);
  if (!assigneeAgentKnown) {
    assigneeOptions.push({ value: ASSIGNEE_AI_PREFIX + assigneeAgentKey,
                           label: assigneeAgentKey, sublabel: 'Agente de IA indisponível' });
  }
  // Transcrição de áudio E descrição de imagem são multi-selects de direção
  // (recebidas/enviadas/privadas), resolvidos pela mesma escada do backend.
  const audioModes = mediaModesFrom(ai, 'audio');
  const audioOff = audioModes.size === 0;
  const toggleAudioMode = (token, on) => {
    const next = new Set(audioModes);
    if (on) next.add(token); else next.delete(token);
    set('audio_transcription_mode', serializeMediaModes(next));
  };
  const imageModes = mediaModesFrom(ai, 'image');
  const imageOff = imageModes.size === 0;
  const toggleImageMode = (token, on) => {
    const next = new Set(imageModes);
    if (on) next.add(token); else next.delete(token);
    // Mantém o booleano legado em sincronia com a direção "Recebidas" (era o único
    // significado que ele tinha): um core anterior ao plano 118, ou um downgrade,
    // continua lendo a intenção do operador para a mídia de entrada.
    onChange({
      ...ai,
      image_transcription_mode: serializeMediaModes(next),
      image_transcription_enabled: next.has('received'),
    });
  };
  // Sequential reply toggle (per channel). When the channel hasn't set it yet,
  // fall back to ``sequentialDefault`` (GOWA → on, other providers → off on new
  // channels; legacy channels inherit "on" to preserve prior always-active behavior).
  const seqOn = ai.ai_sequential_enabled ?? sequentialDefault;
  return html`
    <div class="flex flex-col gap-3">
      <!-- Master switch for this channel -->
      <label class="flex items-center gap-3 text-[14px] font-semibold text-wa-text cursor-pointer p-3 rounded-lg border ${aiOn ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}">
        <input type="checkbox" checked=${aiOn}
          onChange=${(e) => set('ai_enabled', e.target.checked)}
          class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
        Ativar a IA neste canal
      </label>

      <!-- Atendente padrão para novas conversas (plano 71 · plano 152). SEMPRE visível
           — vale mesmo com a IA do canal desligada (o consumidor de integração externa tem
           a IA off): a conversa nova nasce atribuída a este humano e com a IA desligada.
           Desde o plano 152 o mesmo campo aceita um AGENTE DE IA, e aí a conversa nasce
           do outro jeito: vinculada ao agente e com a IA LIGADA. Os agentes de IA só são
           oferecidos com o master do canal ligado (ver offeredAiAgents acima).
           ATENÇÃO: nenhuma CRASE pode aparecer neste comentário — ele está DENTRO do
           template do htm, e uma crase o fecharia, quebrando o módulo inteiro. -->
      <div>
        <label class="block text-[12px] text-wa-secondary mb-1">Atendente padrão para novas conversas</label>
        <${SearchableSelect}
          value=${assigneeVal}
          onChange=${pickAssignee}
          options=${assigneeOptions}
          allowEmpty=${true} emptyLabel='Nenhum (fila "Não atribuídas")'
          placeholder='Nenhum (fila "Não atribuídas")'
          searchPlaceholder="Pesquisar atendente ou agente de IA…" />
        ${assigneeAgentKey ? html`
          <span class="block text-[11px] text-wa-secondary mt-1">A conversa nasce com a IA ligada e atendida por este agente, sem dono humano.</span>
        ` : (ai.default_assignee_user_id != null ? html`
          <span class="block text-[11px] text-wa-secondary mt-1">A conversa nasce atribuída a esta pessoa, com a IA desligada.</span>
        ` : html`
          <span class="block text-[11px] text-wa-secondary mt-1">Pode ser uma pessoa do painel ou um agente de IA. Sem escolha, a conversa nasce na fila "Não atribuídas".</span>
        `)}
        ${assigneeAgentKey && !aiOn ? html`
          <span class="block text-[11px] text-amber-600 dark:text-amber-400 mt-1">Este agente de IA não vai assumir nada enquanto "Ativar a IA neste canal" estiver desligado.</span>
        ` : null}
        ${assigneeAgentKey && !assigneeAgentKnown ? html`
          <span class="block text-[11px] text-amber-600 dark:text-amber-400 mt-1">Agente não encontrado ou desativado — as conversas vão nascer na fila "Não atribuídas". Escolha outro.</span>
        ` : null}
      </div>

      <!-- Transcrição de mídia (plano 118 · D2). SEMPRE visível — igual ao
           "Atendente padrão" acima: o backend NUNCA gateou transcrição pela IA do
           canal (maybe_transcribe não lê ai_enabled nem auto_reply, e no lote ela
           roda ANTES do gate), então esconder os campos com a IA off só tirava do
           operador o único lugar de configurá-los.
           ATENÇÃO: nenhuma CRASE pode aparecer neste comentário — ele está DENTRO
           do template do htm, e uma crase o fecharia, quebrando o módulo inteiro
           (a tela de Canais para de abrir e o modal nunca aparece). -->
      <div class="flex flex-col gap-2 p-3 bg-wa-bg rounded-lg border border-wa-border">
        <div class="text-[13px] font-semibold text-wa-text">Transcrição de mídia</div>
        <span class="text-[11px] text-wa-secondary">Vale mesmo com a IA deste canal desligada. Cada transcrição/descrição consome crédito do LLM.</span>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1.5">Descrever imagem</label>
          <div class="flex flex-col gap-1.5">
            <label class="flex items-center gap-2 text-[13px] text-wa-text cursor-pointer">
              <input type="checkbox" checked=${imageModes.has('received')}
                onChange=${(e) => toggleImageMode('received', e.target.checked)}
                class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
              Recebidas
            </label>
            <label class="flex items-center gap-2 text-[13px] text-wa-text cursor-pointer">
              <input type="checkbox" checked=${imageModes.has('sent')}
                onChange=${(e) => toggleImageMode('sent', e.target.checked)}
                class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
              Enviadas (pelo painel ou pelo celular)
            </label>
            <label class="flex items-center gap-2 text-[13px] text-wa-text cursor-pointer">
              <input type="checkbox" checked=${imageModes.has('private')}
                onChange=${(e) => toggleImageMode('private', e.target.checked)}
                class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
              Privadas (imagens em nota privada)
            </label>
          </div>
          ${imageOff ? html`<span class="block text-[11px] text-wa-secondary mt-1">Nenhuma marcada — descrição desativada.</span>` : null}
          <span class="block text-[11px] text-wa-secondary mt-1">A descrição aparece sempre como card privado, só no painel.</span>
        </div>
        <label class="flex items-center gap-3 text-[13px] text-wa-text cursor-pointer">
          <input type="checkbox" checked=${ai.document_transcription_enabled !== false}
            onChange=${(e) => set('document_transcription_enabled', e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
          Ler documento
        </label>

        <div class="flex flex-col gap-2 pt-2 mt-1 border-t border-wa-border">
          <div class="text-[13px] font-semibold text-wa-text">Transcrição de áudio</div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label class="block text-[12px] text-wa-secondary mb-1.5">Transcrever mensagens</label>
              <div class="flex flex-col gap-1.5">
                <label class="flex items-center gap-2 text-[13px] text-wa-text cursor-pointer">
                  <input type="checkbox" checked=${audioModes.has('received')}
                    onChange=${(e) => toggleAudioMode('received', e.target.checked)}
                    class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
                  Recebidas
                </label>
                <label class="flex items-center gap-2 text-[13px] text-wa-text cursor-pointer">
                  <input type="checkbox" checked=${audioModes.has('sent')}
                    onChange=${(e) => toggleAudioMode('sent', e.target.checked)}
                    class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
                  Enviadas
                </label>
                <label class="flex items-center gap-2 text-[13px] text-wa-text cursor-pointer">
                  <input type="checkbox" checked=${audioModes.has('private')}
                    onChange=${(e) => toggleAudioMode('private', e.target.checked)}
                    class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
                  Privadas (áudios do operador)
                </label>
              </div>
              ${audioOff ? html`<span class="block text-[11px] text-wa-secondary mt-1">Nenhuma marcada — transcrição desativada.</span>` : null}
            </div>
            <div>
              <label class="block text-[12px] text-wa-secondary mb-1">Onde aparece a transcrição</label>
              <select class="wa-field w-full px-3 py-2 rounded-md text-[14px] disabled:opacity-50"
                value=${ai.audio_transcription_target || 'private'}
                disabled=${audioOff}
                onChange=${(e) => set('audio_transcription_target', e.target.value)}>
                <option value="private">Mensagem privada (só no painel)</option>
                <option value="chat">Direto no chat (envia ao contato)</option>
              </select>
              <span class="block text-[11px] text-wa-secondary mt-1">Vale para recebidas/enviadas. Áudios privados ficam sempre só no painel.</span>
            </div>
          </div>
          ${!audioOff && ai.audio_transcription_target === 'chat' ? html`
            <div>
              <label class="block text-[12px] text-wa-secondary mb-1">Prefixo (opcional)</label>
              <textarea rows="2" placeholder="Ex: 🎙 Transcrição: "
                class="wa-field w-full px-3 py-2 rounded-md text-[14px] resize-none"
                value=${ai.audio_transcription_chat_prefix || ''}
                onInput=${(e) => set('audio_transcription_chat_prefix', e.target.value)}></textarea>
            </div>
          ` : null}
        </div>
      </div>

      ${aiOn ? html`
        <label class="flex items-center gap-3 text-[13px] text-wa-text cursor-pointer">
          <input type="checkbox" checked=${ai.default_ai_enabled !== false}
            onChange=${(e) => set('default_ai_enabled', e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
          IA ativada por padrão para novos contatos
        </label>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Resposta da IA em grupos</label>
          <select class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            value=${ai.group_reply_mode || 'mention_only'}
            onChange=${(e) => set('group_reply_mode', e.target.value)}>
            <option value="mention_only">Somente quando o bot for mencionado</option>
            <option value="always">Sempre (responder a todas as mensagens do grupo)</option>
            <option value="never">Nunca (não responder em grupos)</option>
          </select>
        </div>

        <!-- Contexto + agrupamento -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Mensagens de contexto</label>
            <input type="number" min="2" max="100"
              class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              value=${ai.max_context_messages ?? 10}
              onInput=${(e) => num('max_context_messages', e.target.value, 10)} />
            <span class="text-[11px] text-wa-secondary">Qtd de msgs enviadas ao LLM</span>
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Agrupar mensagens (s)</label>
            <input type="number" min="0" max="30" step="0.5"
              class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              value=${ai.message_batch_delay ?? 3}
              onInput=${(e) => num('message_batch_delay', e.target.value, 0)} />
            <span class="text-[11px] text-wa-secondary">Espera antes de responder</span>
          </div>
        </div>

        <!-- Modo sequencial (anti-bloqueio) — ligável/desligável por canal -->
        <div class="flex flex-col gap-2 p-3 bg-wa-bg rounded-lg border border-wa-border">
          <label class="flex items-center gap-2 text-[13px] font-semibold text-wa-text cursor-pointer">
            <input type="checkbox" checked=${seqOn}
              onChange=${(e) => set('ai_sequential_enabled', e.target.checked)}
              class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
            Resposta sequencial (anti-bloqueio)
          </label>
          <span class="text-[11px] text-wa-secondary">A IA nunca responde dois contatos ao mesmo tempo neste canal — reduz o risco de bloqueio do WhatsApp/Meta por envios em paralelo.</span>
          ${seqOn ? html`
            <div>
              <label class="block text-[12px] text-wa-secondary mb-1">Intervalo entre respostas (s)</label>
              <input type="number" min="2" step="1"
                class="wa-field w-32 px-3 py-1.5 rounded-md text-[14px]"
                value=${ai.ai_sequential_delay ?? 2}
                onInput=${(e) => num('ai_sequential_delay', e.target.value, 2)} />
              <span class="block text-[11px] text-wa-secondary mt-1">Espera aplicada antes de cada resposta (mínimo 2s, sem limite).</span>
            </div>
          ` : null}
        </div>

        <!-- Mensagens picadas -->
        <div class="flex flex-col gap-2 p-3 bg-wa-bg rounded-lg border border-wa-border">
          <label class="flex items-center gap-2 text-[13px] text-wa-text cursor-pointer">
            <input type="checkbox" checked=${ai.split_messages !== false}
              onChange=${(e) => set('split_messages', e.target.checked)}
              class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
            Mensagens picadas (dividir resposta)
          </label>
          ${ai.split_messages !== false ? html`
            <div>
              <label class="block text-[12px] text-wa-secondary mb-1">Intervalo entre mensagens (s)</label>
              <input type="number" min="0" max="10" step="0.5"
                class="wa-field w-32 px-3 py-1.5 rounded-md text-[14px]"
                value=${ai.split_message_delay ?? 2}
                onInput=${(e) => num('split_message_delay', e.target.value, 0)} />
            </div>
          ` : null}
        </div>

      ` : null}
    </div>
  `;
}
