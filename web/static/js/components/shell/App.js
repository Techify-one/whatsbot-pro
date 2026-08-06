// App-shell App component (Plano 23 · D4), extracted verbatim from app.js. Owns
// the top-level state, the WebSocket subscriptions (via useWebSocket → the
// singleton wsBus), the routing/popstate wiring, the first-run wizard latch, the
// browser-tab unread badge, and the notification sound/desktop effects. Renders
// the GearMenu + ScreenRouter + LowBalanceModal + PluginModalHost, and the
// SetupWizard when first-run / reopened. Every effect, dep array, and prop is
// preserved unchanged from the pre-decomposition app.js.
import { h } from 'preact';
import { useState, useEffect, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';
import { PluginModalHost } from '../../plugins/ModalHost.js';
import { Slot } from '../../plugins/Slot.js';
import {
  buildPluginApi,
  isFrontendApiCompatible,
  isPluginServicesCompatible,
} from '../../plugins/api.js';
import { reset as resetRegistry, subscribe as subscribeRegistry, inventory as registryInventory, getRouteOverride } from '../../plugins/registry.js';
import { SetupWizard } from '../SetupWizard.js';
import { LowBalanceModal } from '../LowBalanceModal.js';
import { ChangePasswordModal } from '../ChangePasswordModal.js';
import { useWebSocket } from '../../hooks/useWebSocket.js';
import { useConfig } from '../../hooks/useConfig.js';
import { entityFromPath } from '../../hooks/useDeepLink.js';
import { authHeaders, getUnreadCount } from '../../services/api.js';
import { shouldNotifyNewMessage } from '../../services/conversationRows.js';
import { isModifiedClick, spaLinkTarget } from '../../services/spaLink.js';
import * as soundEngine from '../../utils/soundEngine.js';
import { getNotifPref, showBrowserNotification } from '../../utils/notifications.js';
import { GearMenu } from './GearMenu.js';
import { ScreenRouter } from './ScreenRouter.js';
import { Toaster } from './Toaster.js';
import {
  pluginTabId, tabFromPath, pathForTab, redirectLegacyPath,
  contactIdFromPath, conversationIdFromPath, scrollMsgFromSearch,
} from './screenRegistry.js';

const html = htm.bind(h);

const LOW_BALANCE_SNOOZE_KEY = 'whatsbot_low_balance_snoozed_until';

function lowBalanceIsSnoozed() {
  try {
    const v = parseInt(localStorage.getItem(LOW_BALANCE_SNOOZE_KEY) || '0', 10);
    return v && Date.now() < v;
  } catch { return false; }
}

function snoozeLowBalance(ms) {
  try {
    localStorage.setItem(LOW_BALANCE_SNOOZE_KEY, String(Date.now() + ms));
  } catch {}
}

// Frontend extension layer: import each enabled plugin's `frontend_extends` ES
// module once and call its default export `register(api)`. Resets the registry
// first so a re-fetch (after a plugin toggle) re-registers from the current
// manifest only. Failures are isolated — a broken plugin never breaks the app.
async function loadPluginExtensions(plugins) {
  resetRegistry();
  for (const p of (plugins || [])) {
    if (!p || !p.frontend_extends) continue;
    if (!isFrontendApiCompatible(p.frontend_api_version)) {
      console.warn(`[plugins] ${p.id}: frontend_api_version "${p.frontend_api_version}" incompatible — skipping extends`);
      continue;
    }
    if (!isPluginServicesCompatible(p.plugin_services_version)) {
      console.warn(`[plugins] ${p.id}: plugin_services_version "${p.plugin_services_version}" incompatible — skipping extends`);
      continue;
    }
    try {
      const mod = await import(p.frontend_extends);
      const register = mod && (mod.default || mod.register);
      if (typeof register === 'function') {
        await register(buildPluginApi(p.id, p.plugin_services_version));
      } else {
        console.warn(`[plugins] ${p.id}: extends module has no default export (register fn)`);
      }
    } catch (e) {
      console.warn(`[plugins] ${p.id}: failed to load extends module`, e);
    }
  }
  try { window.__whatsbotExtensions = registryInventory(); } catch (_) { /* ignore */ }
}

export function App({ onLogout, hasPassword, currentUser }) {
  const [status, setStatus] = useState({ connected: false, msg_count: 0, auto_reply_running: false });
  const [qrAvailable, setQrAvailable] = useState(false);
  const [qrVersion, setQrVersion] = useState(0);
  const [notification, setNotification] = useState('Iniciando...');
  const [wsConnected, setWsConnected] = useState(true);
  const [pluginScreens, setPluginScreens] = useState([]);
  // Bumped whenever the plugin extension registry changes (slots/filters/route
  // overrides) so route-override resolution and <Slot>s re-render once the async
  // extends modules register (they load after first paint).
  const [extVersion, setExtVersion] = useState(0);
  // True once frontend extension modules have finished loading (or the manifest
  // request failed). It prevents an override-only route from rendering its
  // fallback during the asynchronous registration window.
  const [extensionsLoaded, setExtensionsLoaded] = useState(false);
  const [showChangePassword, setShowChangePassword] = useState(false);  // self-service password modal (plano 47)
  const [tab, setTabState] = useState(() => tabFromPath([]));
  const [unreadConvos, setUnreadConvos] = useState(0);  // conversations with unread msgs (tab-title badge)
  const [newMessage, setNewMessage] = useState(null);
  const [chatPresence, setChatPresence] = useState(null);
  const [aiTyping, setAiTyping] = useState(null);   // {phone, channel_id, active} — IA processando
  const [contactInfoUpdated, setContactInfoUpdated] = useState(null);
  const [tagsChanged, setTagsChanged] = useState(null);
  const [contactTagsUpdated, setContactTagsUpdated] = useState(null);
  const [contactAiToggled, setContactAiToggled] = useState(null);
  const [messagesRead, setMessagesRead] = useState(null);
  const [messageStatus, setMessageStatus] = useState(null);
  const [messageAction, setMessageAction] = useState(null);
  const [messageReaction, setMessageReaction] = useState(null);
  const [avatarUpdated, setAvatarUpdated] = useState(null);
  const [groupParticipantsChanged, setGroupParticipantsChanged] = useState(null);
  const [conversationCreated, setConversationCreated] = useState(null);  // nudge sidebar to materialise a new per-channel thread
  const [lowBalance, setLowBalance] = useState(null);
  const [initialContactId, setInitialContactId] = useState(contactIdFromPath);
  const [initialConversationId, setInitialConversationId] = useState(conversationIdFromPath);
  // Mensagem-alvo do permalink (?message=<_id>): scroll + destaque ao abrir o atendimento.
  const [initialScrollMsgId, setInitialScrollMsgId] = useState(scrollMsgFromSearch);
  // Seleção de entidade vinda da URL (deep-link genérico das demais telas).
  const [initialEntity, setInitialEntity] = useState(entityFromPath);
  const [wizardManual, setWizardManual] = useState(() => window.location.pathname === '/wizard');
  const wizardLatchRef = useRef(false);

  // Open/close the setup wizard, keeping the /wizard URL in sync so it can be
  // reached directly (and bookmarked / shared).
  const openWizard = useCallback(() => {
    setWizardManual(true);
    if (window.location.pathname !== '/wizard') history.pushState(null, '', '/wizard');
  }, []);
  const closeWizard = useCallback(() => {
    wizardLatchRef.current = false;
    setWizardManual(false);
    if (window.location.pathname === '/wizard') {
      history.pushState(null, '', '/');
      window.dispatchEvent(new PopStateEvent('popstate'));
    }
  }, []);

  // Fetch the public plugin manifest once at boot. Errors are non-fatal —
  // the core app keeps running even if plugins fail to load.
  useEffect(() => {
    fetch('/api/plugins/manifest', { headers: authHeaders() })
      .then(r => r.json())
      .then(res => {
        if (!res || !res.ok) { setExtensionsLoaded(true); return; }
        const plugins = res.data.plugins || [];
        const screens = plugins.flatMap(p =>
          (p.screens || [])
            .filter(s => !s.config)  // config screens live in the Plugins tab, not the gear menu
            .map(s => ({ ...s, pluginId: s.pluginId || p.id }))
        );
        setPluginScreens(screens);
        // Load plugin frontend-extension modules (filters / UI slots / route overrides).
        loadPluginExtensions(plugins).finally(() => setExtensionsLoaded(true));
        // Re-evaluate tab now that we know about plugin paths.
        setTabState(tabFromPath(screens));
      })
      .catch(() => { setExtensionsLoaded(true); /* plugin extensions are optional */ });
  }, []);

  // Re-render when the extension registry mutates (extends modules register after
  // first paint; route overrides / slots must take effect immediately).
  useEffect(() => subscribeRegistry(() => setExtVersion(v => v + 1)), []);

  const setTab = useCallback((t) => {
    setTabState(t);
    const path = pathForTab(t, pluginScreens);
    if (window.location.pathname !== path) {
      history.pushState(null, '', path);
      // pushState doesn't fire popstate; notify listeners (e.g. Executions
      // syncs its detail view with the URL).
      window.dispatchEvent(new PopStateEvent('popstate'));
    }
  }, [pluginScreens]);

  // Boot: rewrite any legacy PT URL (/contatos, /painel, …) to its English path
  // before the rest of the app reads window.location.
  useEffect(() => { redirectLegacyPath(); }, []);

  // Plano 64 · F0 — guarda global de arrastar-e-soltar. Sem ela, soltar um
  // arquivo FORA de uma zona de drop faz o navegador navegar para o arquivo e
  // destruir o estado do app (perda do que estava digitado/aberto). Os dois
  // listeners só cancelam o default do navegador; as zonas de drop reais
  // (overlay da conversa, linha da sidebar) continuam recebendo o evento
  // normalmente — elas rodam antes, na fase de bubbling.
  useEffect(() => {
    function swallow(e) {
      if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) {
        e.preventDefault();
      }
    }
    window.addEventListener('dragover', swallow);
    window.addEventListener('drop', swallow);
    return () => {
      window.removeEventListener('dragover', swallow);
      window.removeEventListener('drop', swallow);
    };
  }, []);

  // Plano 106 · F2 — interceptor delegado de links internos. Qualquer <a href="/…">
  // do painel — do core OU de um plugin, SEM o plugin fazer nada — passa a navegar
  // por SPA no clique simples e a ser entregue ao navegador no clique modificado
  // (Ctrl/⌘ = nova guia, Shift = nova janela, Alt = baixar). É o que remove a
  // necessidade de repetir o guard do GearMenu em cada ponto de navegação.
  //
  // É um listener de BUBBLING no document, então roda DEPOIS dos onClick dos
  // componentes: quem já chamou preventDefault (ex.: copyDeepLink) continua
  // vencendo. Quem decide se a âncora é nossa é o predicado puro spaLinkTarget —
  // link externo, target, download, mailto:/tel: e data-no-spa saem intactos.
  useEffect(() => {
    // Base = a URL COMPLETA (não só o origin): além de comparar o host, resolve
    // corretamente um href relativo que uma tela de plugin venha a usar.
    function targetFor(el) {
      const a = el && el.closest ? el.closest('a[href]') : null;
      if (!a) return null;
      return spaLinkTarget({
        href: a.getAttribute('href'),
        target: a.getAttribute('target'),
        // hasAttribute, NÃO a.download: a propriedade devolve '' tanto para
        // ausente quanto para `<a download>` sem valor, e não distingue os dois.
        download: a.hasAttribute('download'),
        dataset: a.dataset,
      }, window.location.href);
    }

    function onClick(e) {
      if (isModifiedClick(e) || e.defaultPrevented) return;
      const target = targetFor(e.target);
      if (!target) return;
      e.preventDefault();
      const here = window.location.pathname + window.location.search + window.location.hash;
      // Um clique = um passo no "voltar": não empilha quando já estamos no destino
      // (o call site pode ter empurrado a URL antes de o evento borbulhar até aqui).
      if (here !== target.path) history.pushState(null, '', target.path);
      // pushState não dispara popstate; é este par que todo call site já usa e que
      // o efeito de rota abaixo escuta.
      window.dispatchEvent(new PopStateEvent('popstate'));
    }

    // Clique do meio: o Chrome inicia o auto-scroll no mousedown. Cancelar só ele
    // (button === 1) sobre link interno deixa o navegador abrir a guia no auxclick.
    function onMouseDown(e) {
      if (e.button !== 1 || e.defaultPrevented) return;
      if (targetFor(e.target)) e.preventDefault();
    }

    document.addEventListener('click', onClick);
    document.addEventListener('mousedown', onMouseDown);
    return () => {
      document.removeEventListener('click', onClick);
      document.removeEventListener('mousedown', onMouseDown);
    };
  }, []);

  useEffect(() => {
    function onPopState() {
      redirectLegacyPath();
      setTabState(tabFromPath(pluginScreens));
      setInitialContactId(contactIdFromPath());
      setInitialConversationId(conversationIdFromPath());
      setInitialScrollMsgId(scrollMsgFromSearch());
      setInitialEntity(entityFromPath());
      setWizardManual(window.location.pathname === '/wizard');
    }
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [pluginScreens]);

  const { config, loading, saving, save, reload: reloadConfig } = useConfig();

  const configRef = useRef(config);
  useEffect(() => { configRef.current = config; }, [config]);

  // First run (no API key, setup not completed): reflect the wizard in the
  // URL so a hard reload / share lands back on /wizard.
  useEffect(() => {
    const firstRun = config && config.setup_completed !== true && !config.openrouter_api_key;
    if (firstRun && window.location.pathname !== '/wizard') {
      history.replaceState(null, '', '/wizard');
    }
  }, [config]);

  useWebSocket({
    onStatus: useCallback((data) => setStatus(data), []),
    onQrUpdate: useCallback((data) => {
      setQrAvailable(data.available);
      if (data.version) setQrVersion(data.version);
    }, []),
    onGowaStatus: useCallback((data) => setNotification(data.message), []),
    onConfigSaved: useCallback(() => { setNotification('Configurações salvas!'); reloadConfig(); soundEngine.reloadPrefs(); }, [reloadConfig]),
    onNewMessage: useCallback((data) => setNewMessage(data), []),
    onChatPresence: useCallback((data) => setChatPresence(data), []),
    onAiTyping: useCallback((data) => setAiTyping(data), []),
    onContactInfoUpdated: useCallback((data) => setContactInfoUpdated(data), []),
    onTagsChanged: useCallback((data) => setTagsChanged(data), []),
    onContactTagsUpdated: useCallback((data) => setContactTagsUpdated(data), []),
    onHumanTransferAlert: useCallback((data) => {
      // Per-channel alert (plano 21): the payload carries the channel's resolved
      // setting; fall back to the global config for older payloads.
      const cfg = configRef.current;
      const enabled = (data && data.enabled !== undefined)
        ? data.enabled
        : !(cfg && cfg.transfer_alert_enabled === false);
      if (!enabled) return;
      const duration = (data && data.duration) || cfg?.transfer_alert_duration || 5;
      // plano 63 F2 — motor unificado (som/volume da sirene agora configuráveis).
      // O servidor já silenciou acima (`if (!enabled) return`); passamos a duração
      // resolvida como override.
      soundEngine.playEvent('ia_to_human', { enabledOverride: enabled, durationOverride: duration });
    }, []),
    onContactAiToggled: useCallback((data) => setContactAiToggled(data), []),
    onMessagesRead: useCallback((data) => setMessagesRead(data), []),
    onMessageStatus: useCallback((data) => setMessageStatus(data), []),
    onMessageAction: useCallback((data) => setMessageAction(data), []),
    onMessageReaction: useCallback((data) => setMessageReaction(data), []),
    onAvatarUpdated: useCallback((data) => setAvatarUpdated(data), []),
    onGroupParticipantsChanged: useCallback((data) => setGroupParticipantsChanged({ ...data, _t: Date.now() }), []),
    onConversationChanged: useCallback((name, data) => {
      if (name === 'conversation_created') setConversationCreated({ ...data, _t: Date.now() });
    }, []),
    onLowBalance: useCallback((data) => {
      if (lowBalanceIsSnoozed()) return;
      setLowBalance(data);
    }, []),
    onWsConnect: useCallback(() => setWsConnected(true), []),
    onWsDisconnect: useCallback(() => setWsConnected(false), []),
  });

  // One-shot balance check on boot — covers the case where the app opens while
  // already below the threshold but no LLM call has happened since the last
  // broadcast. Skipped when the user has snoozed the popup.
  useEffect(() => {
    if (!config || !config.openrouter_api_key) return;
    if (lowBalanceIsSnoozed()) return;
    fetch('/api/balance', { headers: authHeaders() })
      .then(r => r.json())
      .then(res => {
        // plano 42 C: available===false is the degraded shape (proxy down, no
        // cache) — never open the modal for it (it also lacks below_threshold).
        if (res && res.ok && res.data && res.data.available !== false && res.data.low_balance_enabled && res.data.below_threshold) {
          setLowBalance({
            remaining: res.data.remaining,
            total_credits: res.data.total_credits,
            total_usage: res.data.total_usage,
            threshold: res.data.threshold,
            account_url: res.data.account_url,
          });
        }
      })
      .catch(() => { /* ignore */ });
  }, [config && config.openrouter_api_key]);

  // ── Browser-tab unread badge ("(3) WhatsBot"), like WhatsApp Web ──────────
  // Single source of truth is the backend count; we refresh it (debounced) on
  // boot, on WS events that change unread state, and when the contacts list
  // reports a change (e.g. the operator opened/read a chat — no WS event fires
  // for that on the same client).
  const unreadTimerRef = useRef(null);
  const refreshUnreadCount = useCallback(() => {
    if (unreadTimerRef.current) clearTimeout(unreadTimerRef.current);
    unreadTimerRef.current = setTimeout(async () => {
      try {
        const res = await getUnreadCount();
        if (res && res.ok) setUnreadConvos(res.data.count || 0);
      } catch (_) { /* ignore */ }
    }, 250);
  }, []);

  useEffect(() => { refreshUnreadCount(); }, [newMessage, messagesRead, refreshUnreadCount]);

  // Bumped when notification prefs change in the config panel, so the effects
  // below re-evaluate (e.g. turning the tab badge off should apply at once).
  const [notifVersion, setNotifVersion] = useState(0);
  useEffect(() => {
    const onPrefs = () => setNotifVersion(v => v + 1);
    window.addEventListener('whatsbot:notif-prefs', onPrefs);
    return () => window.removeEventListener('whatsbot:notif-prefs', onPrefs);
  }, []);

  // plano 63 — carrega o override de som do usuário + o padrão global da equipe
  // no boot (antes disso o motor usa os code-seeds, nunca fica mudo). A tela
  // "Notificações e sons" chama reloadPrefs() após salvar.
  useEffect(() => { soundEngine.reloadPrefs(); }, []);

  // Tab-title badge — gated by the "tab notification" preference.
  useEffect(() => {
    const tabBadge = getNotifPref('tab');
    document.title = (tabBadge && unreadConvos > 0) ? `(${unreadConvos}) WhatsBot-Pro` : 'WhatsBot-Pro';
  }, [unreadConvos, notifVersion]);

  // Browser notification + sound on a new INBOUND message (from a contact).
  // Sound plays whenever enabled; the desktop notification only shows when the
  // tab isn't visible (you're away), like Telegram/WhatsApp Web.
  useEffect(() => {
    if (!newMessage) return;
    const m = newMessage.message;
    // plano 57: o re-emit AUTORITATIVO pós-save (mesma msg do t=0, com `_id`/`msg_id`
    // reais) NÃO deve re-notificar — o som/alerta já tocou no broadcast do ingest, e o
    // contrato `silent` (ex.: "ignorar abertura") só marca o payload do t=0. Sem este
    // guard, toda mensagem tocaria 2× e uma msg silenciosa tocaria no re-emit.
    if (m && m.authoritative) return;
    // Nota privada (mensagem interna do operador): o ícone verde na conversa e a
    // contagem na aba do navegador são dirigidos pelo backend (unread_count, quando
    // a conta liga `notify_private_messages`) — nada a fazer aqui para eles. O SOM
    // fica DESLIGADO para nota privada hoje. Ponto de extensão futuro, gated pela
    // MESMA config da conta (já chega ao cliente via GET /api/config):
    //   if (configRef.current?.notify_private_messages && getNotifPref('sound')) playNotificationSound();
    if (m && m.role === 'private_note') return;
    if (!m || m.role !== 'user') return;
    // Regra "ignorar abertura" (plugin protocolos): mensagem marcada como silenciosa
    // não gera som nem alerta de nova mensagem (também não conta como não-lida no back).
    if (m.silent) return;
    // Escopo por ATRIBUIÇÃO: só notifica se a conversa é minha ou não tem dono nenhum
    // (nem humano nem IA) — a mensagem da conversa de outro atendente não é minha para
    // atender. Vale para o som E para o pop-up do navegador logo abaixo. O backend manda
    // `assignee_user_id`/`active_agent_key` no payload do ingest; sem eles, notifica
    // como antes (fail-open — ver shouldNotifyNewMessage).
    if (!shouldNotifyNewMessage(m, (currentUser && currentUser.id != null) ? currentUser.id : null)) return;
    // plano 63 F2 — o motor resolve as 3 camadas (usuário/global/dispositivo). O
    // interruptor per-device (`whatsbot_notif_sound`) é checado DENTRO do motor,
    // então o gate legado `getNotifPref('sound')` sai daqui (evita gate duplo).
    soundEngine.playEvent('new_message');
    const away = document.hidden || !document.hasFocus();
    if (getNotifPref('browser') && away) {
      let preview = (m.content || '').trim();
      if (!preview) {
        preview = m.media_type ? 'Enviou uma mídia' : 'Nova mensagem';
      }
      showBrowserNotification('WhatsBot-Pro — nova mensagem', preview.slice(0, 140));
    }
  }, [newMessage]);

  async function handleSave(data) {
    const result = await save(data);
    setNotification(result.message);
  }

  function handleNotify(msg) {
    setNotification(msg);
  }

  if (loading) {
    return html`
      <div class="h-screen flex items-center justify-center">
        <div class="text-center text-wa-secondary animate-pulse-slow">Carregando...</div>
      </div>
    `;
  }

  // First-run setup wizard — takes over the whole screen until completed.
  // Also reopenable on demand via the "Refazer configuração" button on /painel.
  // An install that already has an API key configured is NOT a first run —
  // never ambush an existing/configured user with the wizard after an update.
  const needsSetup = config
    && config.setup_completed !== true
    && !config.openrouter_api_key;
  // Once opened, the wizard stays mounted until the user finishes or closes
  // it — provisioning a key sets openrouter_api_key, which would otherwise
  // flip needsSetup to false mid-flow and unmount the wizard before step 3.
  if (needsSetup || wizardManual) wizardLatchRef.current = true;
  if (wizardLatchRef.current) {
    return html`<${SetupWizard}
      status=${status}
      qrAvailable=${qrAvailable}
      qrVersion=${qrVersion}
      config=${config}
      canClose=${!needsSetup}
      onClose=${closeWizard}
      onConfigSave=${save}
      onComplete=${async () => {
        await save({ setup_completed: true });
        closeWizard();
      }}
    />`;
  }

  // Resolve plugin screen for the current tab id, if any.
  const activePluginScreen = (tab && tab.startsWith('plugin:'))
    ? pluginScreens.find(s => pluginTabId(s) === tab)
    : null;

  // A plugin may claim (override) a CORE route (e.g. 'attendances'); when it does
  // we render its registered component instead of the native screen. `extVersion`
  // is read so this recomputes after async extends modules register.
  void extVersion;
  const activeRouteOverride = (tab && !tab.startsWith('plugin:')) ? getRouteOverride(tab) : null;

  // Seleção de entidade relevante para a tela `t` (ou null). Cada tela só recebe
  // o deep-link da sua própria tab.
  const entFor = (t) => (initialEntity && initialEntity.tab === t ? initialEntity : null);

  // Re-fetch the manifest after a plugin toggle (re-registers extensions from the
  // now-current manifest — a disabled plugin drops out → its slots/overrides go).
  const onPluginsChanged = () => {
    fetch('/api/plugins/manifest', { headers: authHeaders() }).then(r => r.json()).then(res => {
      if (res && res.ok) {
        const plugins = res.data.plugins || [];
        const sc = plugins.flatMap(p =>
          (p.screens || [])
            .filter(s => !s.config)
            .map(s => ({ ...s, pluginId: s.pluginId || p.id }))
        );
        setPluginScreens(sc);
        loadPluginExtensions(plugins);
      }
    });
  };

  // The full prop bundle the chat hub (Contacts) receives — same keys/values as
  // the pre-decomposition inline element (ScreenRouter spreads it).
  const contactsProps = {
    newMessage, chatPresence, aiTyping, contactInfoUpdated, tagsChanged,
    contactTagsUpdated, contactAiToggled, messagesRead, messageStatus,
    messageAction, messageReaction, avatarUpdated, groupParticipantsChanged,
    initialContactId, initialConversationId, initialScrollMsgId, conversationCreated,
    wsConnected, config, onConfigSave: save, onUnreadChange: refreshUnreadCount,
  };

  return html`
    <div class="h-dvh overflow-hidden flex flex-col relative">
      <${GearMenu} tab=${tab} onTabChange=${setTab} pluginScreens=${pluginScreens} hasPassword=${hasPassword} onLogout=${onLogout} accountUrl=${config && config.account_url} currentUser=${currentUser} onChangePassword=${() => setShowChangePassword(true)} />

      <main class="flex-1 min-h-0 overflow-auto ${tab !== 'contacts' ? 'bg-wa-panel' : ''}">
        <${ScreenRouter}
          tab=${tab}
          setTab=${setTab}
          activeRouteOverride=${activeRouteOverride}
          extensionsLoaded=${extensionsLoaded}
          activePluginScreen=${activePluginScreen}
          currentUser=${currentUser}
          config=${config}
          saving=${saving}
          handleSave=${handleSave}
          handleNotify=${handleNotify}
          openWizard=${openWizard}
          entFor=${entFor}
          contactsProps=${contactsProps}
          onPluginsChanged=${onPluginsChanged}
        />
      </main>

      ${lowBalance ? html`<${LowBalanceModal}
        balance=${lowBalance.remaining}
        threshold=${lowBalance.threshold}
        accountUrl=${lowBalance.account_url || (config && config.account_url)}
        onClose=${() => setLowBalance(null)}
        onSnooze=${(ms) => snoozeLowBalance(ms)}
      />` : null}

      ${showChangePassword && currentUser ? html`<${ChangePasswordModal}
        user=${currentUser}
        onNotify=${handleNotify}
        onClose=${() => setShowChangePassword(false)}
      />` : null}

      <!-- Host for plugin-opened modals (e.g. the "popup ao resolver" flow). -->
      <${PluginModalHost} />

      <!-- Host global de toasts (avisos transitórios: 403 "Permissão negada." etc.). -->
      <${Toaster} />

      <!-- Root overlay extension point (P: dev tools): a plugin may register a
           persistent, non-blocking floating widget here via addSlot('app.overlay').
           Renders nothing until a plugin fills it, so it has zero impact by default. -->
      <${Slot} name="app.overlay" ctx=${{ tab, currentUser }} />
    </div>
  `;
}
