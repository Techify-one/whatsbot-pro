import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import { testApiKey, markAllUnread, markAllRead } from '../services/api.js';
import { DatabaseSettings } from './DatabaseSettings.js';

const html = htm.bind(h);

function Section({ title, children }) {
  return html`
    <div class="bg-wa-bg rounded-xl p-5 border border-wa-border shadow-sm">
      ${title ? html`
        <h3 class="text-xs font-semibold text-wa-secondary uppercase tracking-wider mb-4">${title}</h3>
      ` : null}
      <div class="flex flex-col gap-4">
        ${children}
      </div>
    </div>
  `;
}

export function ConfigPanel({ config, saving, onSave, onNotify }) {
  const [apiKey, setApiKey] = useState('');
  const [autoReply, setAutoReply] = useState(true);
  const [audioTranscriptionMode, setAudioTranscriptionMode] = useState('received');
  const [audioTranscriptionTarget, setAudioTranscriptionTarget] = useState('private');
  const [audioTranscriptionChatPrefix, setAudioTranscriptionChatPrefix] = useState('');
  const [imageTranscriptionEnabled, setImageTranscriptionEnabled] = useState(true);
  const [documentTranscriptionEnabled, setDocumentTranscriptionEnabled] = useState(true);
  // Avisos de sistema no chat (plano 12) — toggles globais por grupo de evento.
  const [systemNoticeAssignment, setSystemNoticeAssignment] = useState(true);
  const [systemNoticeTags, setSystemNoticeTags] = useState(true);
  const [systemNoticeConvLabels, setSystemNoticeConvLabels] = useState(true);
  const [systemNoticeStatus, setSystemNoticeStatus] = useState(true);
  const [systemNoticeAi, setSystemNoticeAi] = useState(true);
  const [maxExecutions, setMaxExecutions] = useState(200);
  const [auditRetentionDays, setAuditRetentionDays] = useState(365);
  const [confirmUnreadAll, setConfirmUnreadAll] = useState(false);
  const [markingAllUnread, setMarkingAllUnread] = useState(false);
  const [confirmReadAll, setConfirmReadAll] = useState(false);
  const [markingAllRead, setMarkingAllRead] = useState(false);
  const [defaultAiEnabled, setDefaultAiEnabled] = useState(true);
  const [groupReplyMode, setGroupReplyMode] = useState('mention_only');
  const [testing, setTesting] = useState(false);
  const [webPassword, setWebPassword] = useState('');
  const [webPasswordConfirm, setWebPasswordConfirm] = useState('');
  const [removePassword, setRemovePassword] = useState(false);

  const [saveSuccess, setSaveSuccess] = useState(false);

  // Populate form when config loads
  useEffect(() => {
    if (config) {
      setApiKey(''); // Don't show masked key in input
      setAutoReply(config.auto_reply ?? true);
      setAudioTranscriptionMode(config.audio_transcription_mode ?? 'received');
      setAudioTranscriptionTarget(config.audio_transcription_target ?? 'private');
      setAudioTranscriptionChatPrefix(config.audio_transcription_chat_prefix ?? '');
      setImageTranscriptionEnabled(config.image_transcription_enabled ?? true);
      setDocumentTranscriptionEnabled(config.document_transcription_enabled ?? true);
      setSystemNoticeAssignment(config.system_notice_assignment ?? true);
      setSystemNoticeTags(config.system_notice_tags ?? true);
      setSystemNoticeConvLabels(config.system_notice_conv_labels ?? true);
      setSystemNoticeStatus(config.system_notice_status ?? true);
      setSystemNoticeAi(config.system_notice_ai ?? true);
      setMaxExecutions(config.max_executions ?? 200);
      setAuditRetentionDays(config.audit_retention_days ?? 365);
      setDefaultAiEnabled(config.default_ai_enabled ?? true);
      setGroupReplyMode(config.group_reply_mode ?? 'mention_only');
    }
  }, [config]);

  const [testResult, setTestResult] = useState(null); // {ok, message}

  async function handleTestKey() {
    const key = apiKey.trim();
    if (!key) {
      onNotify('Insira uma API key primeiro.');
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await testApiKey(key);
      if (res.ok) {
        setTestResult({ ok: res.data.valid, message: res.data.message });
        onNotify(res.data.message);
        // Auto-save when key is valid
        if (res.data.valid) {
          await onSave({ openrouter_api_key: key });
        }
      } else {
        setTestResult({ ok: false, message: res.error || 'Erro ao testar.' });
        onNotify(res.error || 'Erro ao testar.');
      }
    } catch {
      setTestResult({ ok: false, message: 'Erro de conexão.' });
      onNotify('Erro de conexão.');
    }
    setTesting(false);
  }

  async function handleMarkAllUnread() {
    setMarkingAllUnread(true);
    try {
      const res = await markAllUnread();
      if (res.ok) {
        onNotify(`${res.data?.count ?? 0} conversa(s) marcada(s) como não lida(s).`);
      } else {
        onNotify(res.error || 'Erro ao marcar conversas.');
      }
    } catch (e) {
      onNotify('Erro de conexão ao marcar conversas.');
    } finally {
      setMarkingAllUnread(false);
      setConfirmUnreadAll(false);
    }
  }

  async function handleMarkAllRead() {
    setMarkingAllRead(true);
    try {
      const res = await markAllRead();
      if (res.ok) {
        onNotify(`${res.data?.count ?? 0} conversa(s) marcada(s) como lida(s).`);
      } else {
        onNotify(res.error || 'Erro ao marcar conversas.');
      }
    } catch (e) {
      onNotify('Erro de conexão ao marcar conversas.');
    } finally {
      setMarkingAllRead(false);
      setConfirmReadAll(false);
    }
  }

  async function handleSave() {
    const data = {
      auto_reply: autoReply,
      audio_transcription_mode: audioTranscriptionMode,
      audio_transcription_target: audioTranscriptionTarget,
      audio_transcription_chat_prefix: audioTranscriptionChatPrefix,
      image_transcription_enabled: imageTranscriptionEnabled,
      document_transcription_enabled: documentTranscriptionEnabled,
      system_notice_assignment: systemNoticeAssignment,
      system_notice_tags: systemNoticeTags,
      system_notice_conv_labels: systemNoticeConvLabels,
      system_notice_status: systemNoticeStatus,
      system_notice_ai: systemNoticeAi,
      max_executions: parseInt(maxExecutions, 10) || 200,
      audit_retention_days: parseInt(auditRetentionDays, 10) || 365,
      default_ai_enabled: defaultAiEnabled,
      group_reply_mode: groupReplyMode,
    };
    // Only include api_key if user typed a new one
    if (apiKey.trim()) {
      data.openrouter_api_key = apiKey.trim();
    }
    // Handle password change/removal
    if (removePassword) {
      data.web_password = '';
    } else if (webPassword.trim()) {
      if (webPassword !== webPasswordConfirm) {
        onNotify('As senhas não coincidem.');
        return;
      }
      data.web_password = webPassword;
    }
    setSaveSuccess(false);
    const result = await onSave(data);
    if (result !== false) {
      setSaveSuccess(true);
      setWebPassword('');
      setWebPasswordConfirm('');
      setRemovePassword(false);
      setTimeout(() => setSaveSuccess(false), 3000);
    }
  }

  if (!config) {
    return html`<div class="bg-wa-bg rounded-xl p-5 animate-pulse-slow text-wa-secondary border border-wa-border">Carregando...</div>`;
  }

  return html`
    <div class="flex flex-col gap-4 flex-1">

      <!-- Section: Automacao -->
      <${Section} title="Automação">
        <label class="flex items-center gap-3 text-sm font-semibold text-wa-text cursor-pointer p-3 rounded-lg border ${autoReply ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}">
          <input
            type="checkbox"
            checked=${autoReply}
            onChange=${(e) => setAutoReply(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          Ativar agente de IA para responder mensagens
        </label>

        <label class="flex items-center gap-3 text-sm font-semibold text-wa-text cursor-pointer p-3 rounded-lg border ${defaultAiEnabled ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}">
          <input
            type="checkbox"
            checked=${defaultAiEnabled}
            onChange=${(e) => setDefaultAiEnabled(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          IA ativada por padrão para novos contatos
        </label>

        <div>
          <label class="block text-sm font-semibold text-wa-text mb-1">Resposta da IA em grupos</label>
          <select
            value=${groupReplyMode}
            onChange=${(e) => setGroupReplyMode(e.target.value)}
            class="w-full bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
          >
            <option value="mention_only">Somente quando o bot for mencionado</option>
            <option value="always">Sempre (responder a todas as mensagens do grupo)</option>
            <option value="never">Nunca (não responder em grupos)</option>
          </select>
          <span class="text-xs text-wa-secondary">Vale apenas para grupos com a IA ativada. "Somente quando mencionado" exige um @menção ao bot; "Sempre" responde a qualquer mensagem do grupo.</span>
        </div>
      <//>

      <!-- Section: API e Modelos -->
      <${Section} title="API e Modelos">
        <!-- API Key -->
        <div>
          <label class="block text-sm font-semibold text-wa-text mb-1">Chave de API Techify</label>
          <div class="flex gap-2">
            <input
              type="password"
              value=${apiKey}
              onInput=${(e) => setApiKey(e.target.value)}
              placeholder=${config.openrouter_api_key || 'sk-or-...'}
              class="flex-1 bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
            />
            <button
              onClick=${handleTestKey}
              disabled=${testing}
              class="px-4 py-2 bg-wa-panel hover:bg-wa-hover disabled:opacity-50 text-wa-text text-sm rounded-lg transition-colors whitespace-nowrap border border-wa-border"
            >
              ${testing ? '...' : 'Testar'}
            </button>
          </div>
          ${testResult ? html`
            <p class="text-xs mt-1 ${testResult.ok ? 'text-green-600' : 'text-red-500'}">
              ${testResult.ok ? '\u2713' : '\u2717'} ${testResult.message}
            </p>
          ` : config.openrouter_api_key ? html`
            <p class="text-xs mt-1 text-wa-secondary">Chave salva: ${config.openrouter_api_key}</p>
          ` : null}
        </div>

        <!-- Image description toggle -->
        <label class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${imageTranscriptionEnabled ? 'bg-green-50 border-green-200 hover:bg-green-100' : 'bg-wa-panel border-wa-border hover:bg-wa-hover'}">
          <input
            type="checkbox"
            checked=${imageTranscriptionEnabled}
            onChange=${(e) => setImageTranscriptionEnabled(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal mt-0.5"
          />
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class=${imageTranscriptionEnabled ? 'text-green-600' : 'text-wa-secondary'}>
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                <circle cx="8.5" cy="8.5" r="1.5"/>
                <polyline points="21 15 16 10 5 21"/>
              </svg>
              <span class="text-sm font-semibold text-wa-text">Descrever imagem</span>
              <span class="text-xs px-2 py-0.5 rounded-full ${imageTranscriptionEnabled ? 'bg-green-600 text-white' : 'bg-wa-secondary/20 text-wa-secondary'}">
                ${imageTranscriptionEnabled ? 'Ativado' : 'Desativado'}
              </span>
            </div>
            <span class="block text-xs text-wa-secondary mt-1">
              Usa IA para descrever automaticamente o conteúdo de imagens recebidas pelo contato
            </span>
          </div>
        </label>

        <!-- Document transcription toggle -->
        <label class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${documentTranscriptionEnabled ? 'bg-green-50 border-green-200 hover:bg-green-100' : 'bg-wa-panel border-wa-border hover:bg-wa-hover'}">
          <input
            type="checkbox"
            checked=${documentTranscriptionEnabled}
            onChange=${(e) => setDocumentTranscriptionEnabled(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal mt-0.5"
          />
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class=${documentTranscriptionEnabled ? 'text-green-600' : 'text-wa-secondary'}>
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="16" y1="13" x2="8" y2="13"/>
                <line x1="16" y1="17" x2="8" y2="17"/>
                <polyline points="10 9 9 9 8 9"/>
              </svg>
              <span class="text-sm font-semibold text-wa-text">Ler documento</span>
              <span class="text-xs px-2 py-0.5 rounded-full ${documentTranscriptionEnabled ? 'bg-green-600 text-white' : 'bg-wa-secondary/20 text-wa-secondary'}">
                ${documentTranscriptionEnabled ? 'Ativado' : 'Desativado'}
              </span>
            </div>
            <span class="block text-xs text-wa-secondary mt-1">
              Usa IA para extrair o conteúdo de documentos recebidos (PDF, DOCX e arquivos de texto)
            </span>
          </div>
        </label>

        <!-- Audio transcription mode & target -->
        <div class="flex flex-col gap-3 p-3 bg-wa-panel rounded-lg border border-wa-border">
          <div class="text-sm font-semibold text-wa-text">Transcrição de áudio</div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label class="block text-xs font-medium text-wa-text mb-1">Transcrever mensagens</label>
              <select
                value=${audioTranscriptionMode}
                onChange=${(e) => setAudioTranscriptionMode(e.target.value)}
                class="w-full bg-wa-bg text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
              >
                <option value="received">Somente recebidas</option>
                <option value="sent">Somente enviadas</option>
                <option value="both">Nos dois sentidos</option>
                <option value="off">Não transcrever</option>
              </select>
            </div>
            <div>
              <label class="block text-xs font-medium text-wa-text mb-1">Onde aparece a transcrição</label>
              <select
                value=${audioTranscriptionTarget}
                onChange=${(e) => setAudioTranscriptionTarget(e.target.value)}
                disabled=${audioTranscriptionMode === 'off'}
                class="w-full bg-wa-bg text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none disabled:opacity-50"
              >
                <option value="private">Mensagem privada (só no painel)</option>
                <option value="chat">Direto no chat (envia ao contato)</option>
              </select>
            </div>
          </div>
          ${audioTranscriptionMode !== 'off' && audioTranscriptionTarget === 'chat' ? html`
            <div>
              <label class="block text-xs font-medium text-wa-text mb-1">Prefixo (opcional)</label>
              <textarea
                value=${audioTranscriptionChatPrefix}
                onInput=${(e) => setAudioTranscriptionChatPrefix(e.target.value)}
                rows="2"
                placeholder="Ex: 🎙 Transcrição: "
                class="w-full bg-wa-bg text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none resize-none"
              ></textarea>
              <span class="text-xs text-wa-secondary">Texto colado antes da transcrição enviada ao chat. Deixe em branco para enviar só o texto.</span>
            </div>
          ` : null}
        </div>
      <//>

      <!-- Section: Marcar conversas -->
      <${Section} title="Marcar conversas">
        <!-- Mark all read / unread -->
        <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
          <span class="text-xs text-wa-secondary">Reacende ou limpa o indicador verde de não lido no painel. Para uma conversa específica, use o botão direito sobre o contato na lista.</span>
          ${confirmUnreadAll ? html`
            <div class="mt-1 flex flex-col gap-2 p-3 rounded-lg bg-amber-50 border border-amber-300">
              <span class="text-sm font-medium text-amber-800">Marcar TODAS as conversas como não lidas?</span>
              <span class="text-xs text-amber-700">Reacende o indicador verde em todos os contatos do painel. Não afeta o WhatsApp do celular.</span>
              <div class="flex gap-2 mt-1">
                <button
                  type="button"
                  disabled=${markingAllUnread}
                  onClick=${handleMarkAllUnread}
                  class="px-4 py-2 rounded-lg text-sm font-medium bg-amber-600 text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
                >${markingAllUnread ? 'Marcando...' : 'Confirmar'}</button>
                <button
                  type="button"
                  disabled=${markingAllUnread}
                  onClick=${() => setConfirmUnreadAll(false)}
                  class="px-4 py-2 rounded-lg text-sm font-medium bg-wa-bg text-wa-text border border-wa-border hover:bg-wa-hover disabled:opacity-50 transition-colors"
                >Cancelar</button>
              </div>
            </div>
          ` : confirmReadAll ? html`
            <div class="mt-1 flex flex-col gap-2 p-3 rounded-lg bg-amber-50 border border-amber-300">
              <span class="text-sm font-medium text-amber-800">Marcar TODAS as conversas como lidas?</span>
              <span class="text-xs text-amber-700">Remove o indicador verde de não lido de todos os contatos do painel.</span>
              <div class="flex gap-2 mt-1">
                <button
                  type="button"
                  disabled=${markingAllRead}
                  onClick=${handleMarkAllRead}
                  class="px-4 py-2 rounded-lg text-sm font-medium bg-amber-600 text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
                >${markingAllRead ? 'Marcando...' : 'Confirmar'}</button>
                <button
                  type="button"
                  disabled=${markingAllRead}
                  onClick=${() => setConfirmReadAll(false)}
                  class="px-4 py-2 rounded-lg text-sm font-medium bg-wa-bg text-wa-text border border-wa-border hover:bg-wa-hover disabled:opacity-50 transition-colors"
                >Cancelar</button>
              </div>
            </div>
          ` : html`
            <div class="flex flex-wrap gap-2 mt-1">
              <button
                type="button"
                onClick=${() => { setConfirmReadAll(false); setConfirmUnreadAll(true); }}
                class="px-4 py-2 rounded-lg text-sm font-medium bg-wa-teal text-white hover:opacity-90 transition-opacity"
              >Marcar todas como não lidas</button>
              <button
                type="button"
                onClick=${() => { setConfirmUnreadAll(false); setConfirmReadAll(true); }}
                class="px-4 py-2 rounded-lg text-sm font-medium bg-wa-bg text-wa-text border border-wa-border hover:bg-wa-hover transition-colors"
              >Marcar todas como lidas</button>
            </div>
          `}
        </div>
      <//>

      <!-- Section: Avisos de sistema no chat (plano 12) -->
      <${Section} title="Avisos de sistema no chat">
        <span class="text-xs text-wa-secondary -mt-1">
          Registra no fio da conversa, como uma mensagem de sistema, os eventos do atendimento (atribuição, tags, status, IA). Desligar um grupo impede a geração do aviso para todas as conversas — nada é gravado nem exibido.
        </span>
        <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
          <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer">
            <input
              type="checkbox"
              checked=${systemNoticeAssignment}
              onChange=${(e) => setSystemNoticeAssignment(e.target.checked)}
              class="w-4 h-4 rounded border-wa-border accent-wa-teal"
            />
            Atribuição
          </label>
          <span class="text-xs text-wa-secondary">Atribuir, transferir e "assumir para mim".</span>
        </div>
        <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
          <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer">
            <input
              type="checkbox"
              checked=${systemNoticeTags}
              onChange=${(e) => setSystemNoticeTags(e.target.checked)}
              class="w-4 h-4 rounded border-wa-border accent-wa-teal"
            />
            Tags
          </label>
          <span class="text-xs text-wa-secondary">Adicionar ou remover tags de um contato.</span>
        </div>
        <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
          <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer">
            <input
              type="checkbox"
              checked=${systemNoticeConvLabels}
              onChange=${(e) => setSystemNoticeConvLabels(e.target.checked)}
              class="w-4 h-4 rounded border-wa-border accent-wa-teal"
            />
            Etiquetas da conversa
          </label>
          <span class="text-xs text-wa-secondary">Adicionar ou remover etiquetas de uma conversa.</span>
        </div>
        <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
          <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer">
            <input
              type="checkbox"
              checked=${systemNoticeStatus}
              onChange=${(e) => setSystemNoticeStatus(e.target.checked)}
              class="w-4 h-4 rounded border-wa-border accent-wa-teal"
            />
            Status e arquivo
          </label>
          <span class="text-xs text-wa-secondary">Resolver, reabrir (inclusive automática ao receber mensagem), arquivar e iniciar conversa.</span>
        </div>
        <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
          <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer">
            <input
              type="checkbox"
              checked=${systemNoticeAi}
              onChange=${(e) => setSystemNoticeAi(e.target.checked)}
              class="w-4 h-4 rounded border-wa-border accent-wa-teal"
            />
            IA e atributos
          </label>
          <span class="text-xs text-wa-secondary">Ligar/desligar a IA, "a IA assumiu o atendimento", trocar o agente ativo e definir atributos.</span>
        </div>
      <//>

      <!-- Section: Avancado -->
      <${Section} title="Avançado">
        <!-- Max Executions -->
        <div>
          <label class="block text-sm font-semibold text-wa-text mb-1">Execuções salvas</label>
          <input
            type="number"
            min="10"
            max="10000"
            step="10"
            value=${maxExecutions}
            onInput=${(e) => setMaxExecutions(e.target.value)}
            class="w-full bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
          />
          <span class="text-xs text-wa-secondary">Quantidade máxima de execuções e payloads mantidos no banco</span>
        </div>

        <!-- Audit retention -->
        <div>
          <label class="block text-sm font-semibold text-wa-text mb-1">Retenção da auditoria (dias)</label>
          <input
            type="number"
            min="1"
            max="3650"
            step="1"
            value=${auditRetentionDays}
            onInput=${(e) => setAuditRetentionDays(e.target.value)}
            class="w-full bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
          />
          <span class="text-xs text-wa-secondary">Por quantos dias os registros da trilha de auditoria são mantidos</span>
        </div>

        <!-- Panel Password -->
        <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
          <div class="flex items-center justify-between">
            <label class="text-sm font-semibold text-wa-text">Senha do Painel</label>
            ${config.has_password ? html`
              <span class="text-xs bg-wa-teal text-white px-2 py-0.5 rounded-full">Ativa</span>
            ` : html`
              <span class="text-xs bg-wa-secondary/20 text-wa-secondary px-2 py-0.5 rounded-full">Desativada</span>
            `}
          </div>
          <span class="text-xs text-wa-secondary">Protege o acesso ao painel web com senha</span>
          ${!removePassword ? html`
            <input
              type="password"
              value=${webPassword}
              onInput=${(e) => setWebPassword(e.target.value)}
              placeholder=${config.has_password ? 'Nova senha (deixe vazio para manter)' : 'Definir senha'}
              class="w-full bg-wa-bg text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
            />
            ${webPassword ? html`
              <input
                type="password"
                value=${webPasswordConfirm}
                onInput=${(e) => setWebPasswordConfirm(e.target.value)}
                placeholder="Confirmar senha"
                class="w-full bg-wa-bg text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none ${webPassword && webPasswordConfirm && webPassword !== webPasswordConfirm ? 'border-red-400' : ''}"
              />
              ${webPassword && webPasswordConfirm && webPassword !== webPasswordConfirm ? html`
                <span class="text-xs text-red-500">As senhas não coincidem</span>
              ` : null}
            ` : null}
          ` : null}
          ${config.has_password ? html`
            <label class="flex items-center gap-2 text-sm text-red-600 cursor-pointer mt-1">
              <input
                type="checkbox"
                checked=${removePassword}
                onChange=${(e) => { setRemovePassword(e.target.checked); if (e.target.checked) { setWebPassword(''); setWebPasswordConfirm(''); } }}
                class="w-4 h-4 rounded border-wa-border accent-red-600"
              />
              Remover senha
            </label>
          ` : null}
        </div>
      <//>

      <${DatabaseSettings} onNotify=${onNotify} />

      <!-- Save Button (sticky) -->
      <div class="sticky bottom-0 z-10 bg-wa-panel pt-2 pb-1">
        <button
          onClick=${handleSave}
          disabled=${saving}
          class="w-full py-2.5 ${saveSuccess ? 'bg-green-600' : 'bg-wa-teal hover:bg-wa-tealDark'} disabled:opacity-50 text-white font-medium rounded-lg transition-colors shadow-sm"
        >
          ${saving ? 'Salvando...' : saveSuccess ? '\u2713 Salvo!' : 'Salvar Configurações'}
        </button>
      </div>
    </div>
  `;
}
