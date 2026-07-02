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
import { buildPluginApi, isFrontendApiCompatible } from '../../plugins/api.js';
import { reset as resetRegistry, subscribe as subscribeRegistry, inventory as registryInventory, getRouteOverride } from '../../plugins/registry.js';
import { SetupWizard } from '../SetupWizard.js';
import { LowBalanceModal } from '../LowBalanceModal.js';
import { useWebSocket } from '../../hooks/useWebSocket.js';
import { useConfig } from '../../hooks/useConfig.js';
import { entityFromPath } from '../../hooks/useDeepLink.js';
import { authHeaders, getUnreadCount } from '../../services/api.js';
import { playTransferAlert } from '../../utils/alertSound.js';
import { getNotifPref, playNotificationSound, showBrowserNotification } from '../../utils/notifications.js';
import { GearMenu } from './GearMenu.js';
import { ScreenRouter } from './ScreenRouter.js';
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
    try {
      const mod = await import(p.frontend_extends);
      const register = mod && (mod.default || mod.register);
      if (typeof register === 'function') {
        await register(buildPluginApi(p.id));
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
  // Mensagem-alvo do permalink (?message=<_id>): scroll + destaque ao abrir a conversa.
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
        if (!res || !res.ok) return;
        const plugins = res.data.plugins || [];
        const screens = plugins.flatMap(p =>
          (p.screens || [])
            .filter(s => !s.config)  // config screens live in the Plugins tab, not the gear menu
            .map(s => ({ ...s, pluginId: s.pluginId || p.id }))
        );
        setPluginScreens(screens);
        // Load plugin frontend-extension modules (filters / UI slots / route overrides).
        loadPluginExtensions(plugins);
        // Re-evaluate tab now that we know about plugin paths.
        setTabState(tabFromPath(screens));
      })
      .catch(() => { /* ignore */ });
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
    onConfigSaved: useCallback(() => { setNotification('Configurações salvas!'); reloadConfig(); }, [reloadConfig]),
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
      playTransferAlert(duration);
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
        if (res && res.ok && res.data && res.data.low_balance_enabled && res.data.below_threshold) {
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

  // Tab-title badge — gated by the "tab notification" preference.
  useEffect(() => {
    const tabBadge = getNotifPref('tab');
    document.title = (tabBadge && unreadConvos > 0) ? `(${unreadConvos}) WhatsBot` : 'WhatsBot';
  }, [unreadConvos, notifVersion]);

  // Browser notification + sound on a new INBOUND message (from a contact).
  // Sound plays whenever enabled; the desktop notification only shows when the
  // tab isn't visible (you're away), like Telegram/WhatsApp Web.
  useEffect(() => {
    if (!newMessage) return;
    const m = newMessage.message;
    if (!m || m.role !== 'user') return;
    if (getNotifPref('sound')) playNotificationSound();
    const away = document.hidden || !document.hasFocus();
    if (getNotifPref('browser') && away) {
      let preview = (m.content || '').trim();
      if (!preview) {
        preview = m.media_type ? 'Enviou uma mídia' : 'Nova mensagem';
      }
      showBrowserNotification('WhatsBot — nova mensagem', preview.slice(0, 140));
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
      <${GearMenu} tab=${tab} onTabChange=${setTab} pluginScreens=${pluginScreens} hasPassword=${hasPassword} onLogout=${onLogout} accountUrl=${config && config.account_url} currentUser=${currentUser} />

      <main class="flex-1 min-h-0 overflow-auto ${tab !== 'contacts' ? 'bg-wa-panel' : ''}">
        <${ScreenRouter}
          tab=${tab}
          setTab=${setTab}
          activeRouteOverride=${activeRouteOverride}
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

      <!-- Host for plugin-opened modals (e.g. the "popup ao resolver" flow). -->
      <${PluginModalHost} />

      <!-- Root overlay extension point (P: dev tools): a plugin may register a
           persistent, non-blocking floating widget here via addSlot('app.overlay').
           Renders nothing until a plugin fills it, so it has zero impact by default. -->
      <${Slot} name="app.overlay" ctx=${{ tab, currentUser }} />
    </div>
  `;
}
