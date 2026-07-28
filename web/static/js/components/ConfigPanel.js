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

// `sections`: quais seções renderizar (e, portanto, quais chaves o Salvar manda).
// Cada aba de Configurações Gerais monta o painel com a sua fatia — "avisos" na
// aba Geral, "avancado" na aba Avançado. Sem a prop, renderiza todas.
export function ConfigPanel({ config, saving, onSave, onNotify, currentUser, sections }) {
  const show = (id) => !sections || sections.includes(id);
  // Avisos de sistema, execuções salvas, retenção de auditoria e a senha do
  // painel vivem no PUT /api/config (gated settings.manage). Sem essa permissão
  // esses campos NÃO aparecem — só a seção "Marcar conversas" (endpoints próprios).
  // Gate POR SEÇÃO: cada aba de Configurações Gerais tem a sua permissão;
  // `settings.manage` é o super-conjunto que libera todas (e é o que gateia a
  // escrita no PUT /api/config junto com a granular da chave).
  const canAllSettings = hasPermission(currentUser, 'settings.manage');
  const canSection = (id) => canAllSettings || hasPermission(
    currentUser, id === 'avancado' ? 'settings.advanced' : 'settings.general');
  const canSettings = canSection('avisos') || canSection('avancado');
  // Avisos de sistema no chat (plano 12) — toggles globais por grupo de evento.
  const [systemNoticeAssignment, setSystemNoticeAssignment] = useState(true);
  const [systemNoticeTags, setSystemNoticeTags] = useState(true);
  const [systemNoticeConvLabels, setSystemNoticeConvLabels] = useState(true);
  const [systemNoticeStatus, setSystemNoticeStatus] = useState(true);
  const [systemNoticeAi, setSystemNoticeAi] = useState(true);
  const [maxExecutions, setMaxExecutions] = useState(200);
  const [auditRetentionDays, setAuditRetentionDays] = useState(365);
  // URL pública base do painel (domínio real) — usada por links externos (ex.: link
  // do painel de melhorias). Auto-detectada; editável aqui para correção manual.
  const [publicBaseUrl, setPublicBaseUrl] = useState('');

  const [saveSuccess, setSaveSuccess] = useState(false);
  // Deep-link de seção (Plano 24): ?section=<id> rola até a seção ao abrir
  // (avisos | avancado). Roda uma vez, quando o
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
      setMaxExecutions(config.max_executions ?? 200);
      setAuditRetentionDays(config.audit_retention_days ?? 365);
      setPublicBaseUrl(config.public_base_url ?? '');
    }
  }, [config]);

  async function handleSave() {
    const data = {
      ...(show('avisos') ? {
        system_notice_assignment: systemNoticeAssignment,
        system_notice_tags: systemNoticeTags,
        system_notice_conv_labels: systemNoticeConvLabels,
        system_notice_status: systemNoticeStatus,
        system_notice_ai: systemNoticeAi,
      } : {}),
      ...(show('avancado') ? {
        max_executions: parseInt(maxExecutions, 10) || 200,
        audit_retention_days: parseInt(auditRetentionDays, 10) || 365,
        public_base_url: (publicBaseUrl || '').trim().replace(/\/+$/, ''),
      } : {}),
    };
    setSaveSuccess(false);
    const result = await onSave(data);
    if (result !== false) {
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    }
  }

  if (!config) {
    return html`<div class="bg-wa-bg rounded-xl p-5 animate-pulse-slow text-wa-secondary border border-wa-border">Carregando...</div>`;
  }

  return html`
    <div class="flex flex-col gap-4 flex-1">

      ${canSection('avisos') && show('avisos') ? html`
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

      ` : null}

      ${canSection('avancado') && show('avancado') ? html`
      <!-- Section: Avancado -->
      <${Section} id="avancado" title="Avançado">
        <!-- Domínio público (URL base) -->
        <div>
          <label class="block text-sm font-semibold text-wa-text mb-1">Domínio público (URL base)</label>
          <input
            type="url"
            value=${publicBaseUrl}
            onInput=${(e) => setPublicBaseUrl(e.target.value)}
            placeholder="https://seu-dominio.com"
            class="w-full bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
          />
          <span class="text-xs text-wa-secondary">URL usada em links externos gerados pelo painel (ex.: o link do painel de melhorias). Detectada automaticamente ao acessar pelo domínio; ajuste aqui se sair com um IP local. A variável de ambiente WHATSBOT_PUBLIC_URL, se definida, tem prioridade sobre este campo.</span>
        </div>

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
      <//>

      ` : null}

      <!-- Save Button (sticky) — vale para a fatia renderizada nesta aba -->
      ${canSettings ? html`
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
