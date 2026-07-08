import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { hasPermission } from '../utils/permissions.js';

const html = htm.bind(h);

function Section({ title, id, children }) {
  return html`
    <div id=${id} class="bg-wa-bg rounded-xl p-5 border border-wa-border shadow-sm scroll-mt-4">
      ${title ? html`
        <h3 class="text-xs font-semibold text-wa-secondary uppercase tracking-wider mb-4">${title}</h3>
      ` : null}
      <div class="flex flex-col gap-4">
        ${children}
      </div>
    </div>
  `;
}

export function ConfigPanel({ config, saving, onSave, onNotify, currentUser }) {
  // Avisos de sistema, execuções salvas, retenção de auditoria e a senha do
  // painel vivem no PUT /api/config (gated settings.manage). Sem essa permissão
  // esses campos NÃO aparecem — só a seção "Marcar conversas" (endpoints próprios).
  const canSettings = hasPermission(currentUser, 'settings.manage');
  // Avisos de sistema no chat (plano 12) — toggles globais por grupo de evento.
  const [systemNoticeAssignment, setSystemNoticeAssignment] = useState(true);
  const [systemNoticeTags, setSystemNoticeTags] = useState(true);
  const [systemNoticeConvLabels, setSystemNoticeConvLabels] = useState(true);
  const [systemNoticeStatus, setSystemNoticeStatus] = useState(true);
  const [systemNoticeAi, setSystemNoticeAi] = useState(true);
  // Notificação de mensagens privadas (nota interna do operador) — desligado por padrão.
  const [notifyPrivateMessages, setNotifyPrivateMessages] = useState(false);
  const [maxExecutions, setMaxExecutions] = useState(200);
  const [auditRetentionDays, setAuditRetentionDays] = useState(365);
  const [webPassword, setWebPassword] = useState('');
  const [webPasswordConfirm, setWebPasswordConfirm] = useState('');
  const [removePassword, setRemovePassword] = useState(false);

  const [saveSuccess, setSaveSuccess] = useState(false);

  // Deep-link de seção (Plano 24): ?section=<id> rola até a seção ao abrir
  // (avisos | notificacoes | avancado). Roda uma vez, quando o
  // conteúdo já montou (config carregada).
  const sectionScrolledRef = useRef(false);
  useEffect(() => {
    if (!config || sectionScrolledRef.current) return;
    sectionScrolledRef.current = true;
    let section = null;
    try { section = new URLSearchParams(window.location.search).get('section'); } catch {}
    if (!section) return;
    requestAnimationFrame(() => {
      const el = document.getElementById(section);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }, [config]);

  // Populate form when config loads
  useEffect(() => {
    if (config) {
      setSystemNoticeAssignment(config.system_notice_assignment ?? true);
      setSystemNoticeTags(config.system_notice_tags ?? true);
      setSystemNoticeConvLabels(config.system_notice_conv_labels ?? true);
      setSystemNoticeStatus(config.system_notice_status ?? true);
      setSystemNoticeAi(config.system_notice_ai ?? true);
      setNotifyPrivateMessages(config.notify_private_messages ?? false);
      setMaxExecutions(config.max_executions ?? 200);
      setAuditRetentionDays(config.audit_retention_days ?? 365);
    }
  }, [config]);

  async function handleSave() {
    const data = {
      system_notice_assignment: systemNoticeAssignment,
      system_notice_tags: systemNoticeTags,
      system_notice_conv_labels: systemNoticeConvLabels,
      system_notice_status: systemNoticeStatus,
      system_notice_ai: systemNoticeAi,
      notify_private_messages: notifyPrivateMessages,
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

      ${canSettings ? html`
      <!-- Section: Avisos de sistema no chat (plano 12) -->
      <${Section} id="avisos" title="Avisos de sistema no chat">
        <span class="text-xs text-wa-secondary -mt-1">
          Registra no fio da conversa, como uma mensagem de sistema, os eventos da conversa (atribuição, tags, status, IA). Desligar um grupo impede a geração do aviso para todas as conversas — nada é gravado nem exibido.
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
          <span class="text-xs text-wa-secondary">Ligar/desligar a IA, "a IA assumiu a conversa", trocar o agente ativo e definir atributos.</span>
        </div>
      <//>

      <!-- Section: Notificações -->
      <${Section} id="notificacoes" title="Notificações">
        <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
          <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer">
            <input
              type="checkbox"
              checked=${notifyPrivateMessages}
              onChange=${(e) => setNotifyPrivateMessages(e.target.checked)}
              class="w-4 h-4 rounded border-wa-border accent-wa-teal"
            />
            Notificar mensagens privadas
          </label>
          <span class="text-xs text-wa-secondary">Ao adicionar uma nota privada (mensagem interna, não enviada ao contato), acende o ícone verde na conversa e a contagem na aba do navegador. Não toca som.</span>
        </div>
      <//>

      <!-- Section: Avancado -->
      <${Section} id="avancado" title="Avançado">
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
      ` : null}
    </div>
  `;
}
