// Channels — AiSettingsFields (Plano 23 · D4), extracted verbatim from
// ChannelsManager.js. Per-channel AI settings form (config.ai). Controlled:
// `value` is the ai object, `onChange` receives the full updated object. Mirrors
// the knobs that used to be global in the IA "Configurações" tab — now per channel.
import { h } from 'preact';
import htm from 'htm';
import { mediaModesFrom, serializeMediaModes } from './constants.js';
import { SearchableSelect } from '../SearchableSelect.js';

const html = htm.bind(h);

export function AiSettingsFields({ value, onChange, sequentialDefault = true, users = [] }) {
  const ai = value || {};
  const set = (key, v) => onChange({ ...ai, [key]: v });
  // Default human attendant for NEW conversations (plano 71). Stored as an int
  // (or null) in ai.default_assignee_user_id; the <select> works in strings.
  const assigneeVal = ai.default_assignee_user_id != null ? String(ai.default_assignee_user_id) : '';
  const num = (key, v, fallback) => {
    const n = parseFloat(v);
    set(key, isNaN(n) ? fallback : n);
  };
  const aiOn = ai.ai_enabled !== false;
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

      <!-- Atendente padrão para novas conversas (plano 71). SEMPRE visível — vale
           mesmo com a IA do canal desligada (o consumidor de integração externa tem a IA off):
           a conversa nova nasce atribuída a este humano e com a IA desligada. -->
      <div>
        <label class="block text-[12px] text-wa-secondary mb-1">Atendente padrão para novas conversas</label>
        <${SearchableSelect}
          value=${assigneeVal ? String(assigneeVal) : ''}
          onChange=${(v) => set('default_assignee_user_id', v ? parseInt(v, 10) : null)}
          options=${users.map((u) => ({ value: String(u.id), label: u.name || u.email }))}
          allowEmpty=${true} emptyLabel='Nenhum (fila "Não atribuídas")'
          placeholder='Nenhum (fila "Não atribuídas")'
          searchPlaceholder="Pesquisar atendente…" />
        ${assigneeVal ? html`
          <span class="block text-[11px] text-wa-secondary mt-1">A conversa nasce atribuída a esta pessoa, com a IA desligada.</span>
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
