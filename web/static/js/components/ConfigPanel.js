import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import { markAllUnread, markAllRead } from '../services/api.js';
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
  const [webPassword, setWebPassword] = useState('');
  const [webPasswordConfirm, setWebPasswordConfirm] = useState('');
  const [removePassword, setRemovePassword] = useState(false);

  const [saveSuccess, setSaveSuccess] = useState(false);

  // Populate form when config loads
  useEffect(() => {
    if (config) {
      setSystemNoticeAssignment(config.system_notice_assignment ?? true);
      setSystemNoticeTags(config.system_notice_tags ?? true);
      setSystemNoticeConvLabels(config.system_notice_conv_labels ?? true);
      setSystemNoticeStatus(config.system_notice_status ?? true);
      setSystemNoticeAi(config.system_notice_ai ?? true);
      setMaxExecutions(config.max_executions ?? 200);
      setAuditRetentionDays(config.audit_retention_days ?? 365);
    }
  }, [config]);

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
      system_notice_assignment: systemNoticeAssignment,
      system_notice_tags: systemNoticeTags,
      system_notice_conv_labels: systemNoticeConvLabels,
      system_notice_status: systemNoticeStatus,
      system_notice_ai: systemNoticeAi,
      max_executions: parseInt(maxExecutions, 10) || 200,
      audit_retention_days: parseInt(auditRetentionDays, 10) || 365,
    };
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
