import { useEffect } from 'preact/hooks';
import { subscribe } from '../services/wsBus.js';

// Subscribe a consumer to the SINGLETON WebSocket bus (Plano 23 · D4). Previously
// this opened a dedicated socket per mount via `createWebSocket`; now it builds
// the SAME handler map and registers it with the shared bus (services/wsBus.js),
// which owns one connection and fans every event out to every subscriber. The
// handler map shape is unchanged, so every event each consumer reacted to still
// reaches the same callback with the same payload.
export function useWebSocket({ onStatus, onQrUpdate, onGowaStatus, onConfigSaved, onNewMessage, onChatPresence, onAiTyping, onOperatorTyping, onContactInfoUpdated, onTagsChanged, onContactTagsUpdated, onHumanTransferAlert, onAgentTransferAlert, onContactAiToggled, onMessagesRead, onMessageStatus, onMessageAction, onMessageReaction, onAvatarUpdated, onGroupParticipantsChanged, onLowBalance, onConversationChanged, onWsConnect, onWsDisconnect }) {
  useEffect(() => {
    // The 6 conversation lifecycle events (plano 10 FF2) all route to a single
    // onConversationChanged(eventName, data) so the consumer can patch/refetch.
    const conv = onConversationChanged
      ? (name) => (d) => onConversationChanged(name, d)
      : null;
    const unsubscribe = subscribe({
      onConnect: onWsConnect,
      onDisconnect: onWsDisconnect,
      status: onStatus,
      qr_update: onQrUpdate,
      gowa_status: onGowaStatus,
      config_saved: onConfigSaved,
      new_message: onNewMessage,
      chat_presence: onChatPresence,
      ai_typing: onAiTyping,
      // Outro ATENDENTE digitando na conversa (multi-operador) — distinto de
      // `chat_presence` (o cliente) e de `ai_typing` (a IA).
      operator_typing: onOperatorTyping,
      contact_info_updated: onContactInfoUpdated,
      tags_changed: onTagsChanged,
      contact_tags_updated: onContactTagsUpdated,
      human_transfer_alert: onHumanTransferAlert,
      agent_transfer_alert: onAgentTransferAlert,
      contact_ai_toggled: onContactAiToggled,
      messages_read: onMessagesRead,
      message_status: onMessageStatus,
      message_revoked: onMessageAction ? (d) => onMessageAction({ ...d, action: 'revoked' }) : undefined,
      message_deleted: onMessageAction ? (d) => onMessageAction({ ...d, action: 'deleted' }) : undefined,
      message_edited: onMessageAction ? (d) => onMessageAction({ ...d, action: 'edited' }) : undefined,
      message_reaction: onMessageReaction,
      avatar_updated: onAvatarUpdated,
      group_participants_changed: onGroupParticipantsChanged,
      low_balance: onLowBalance,
      conversation_upsert: conv ? conv('conversation_upsert') : undefined,
      conversation_created: conv ? conv('conversation_created') : undefined,
      conversation_status_changed: conv ? conv('conversation_status_changed') : undefined,
      conversation_assigned: conv ? conv('conversation_assigned') : undefined,
      conversation_archived: conv ? conv('conversation_archived') : undefined,
      conversation_pinned: conv ? conv('conversation_pinned') : undefined,
      conversation_ai_toggled: conv ? conv('conversation_ai_toggled') : undefined,
      conversation_updated: conv ? conv('conversation_updated') : undefined,
      conversation_deleted: conv ? conv('conversation_deleted') : undefined,
      conversation_labels_changed: conv ? conv('conversation_labels_changed') : undefined,
      mention_created: conv ? conv('mention_created') : undefined,
    });
    return unsubscribe;
  }, []);
}
