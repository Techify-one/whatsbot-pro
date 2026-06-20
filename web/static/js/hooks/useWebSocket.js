import { useEffect } from 'preact/hooks';
import { createWebSocket } from '../services/websocket.js';

export function useWebSocket({ onStatus, onQrUpdate, onGowaStatus, onConfigSaved, onNewMessage, onChatPresence, onAiTyping, onContactInfoUpdated, onTagsChanged, onContactTagsUpdated, onHumanTransferAlert, onContactAiToggled, onMessagesRead, onMessageStatus, onMessageAction, onMessageReaction, onAvatarUpdated, onGroupParticipantsChanged, onLowBalance, onConversationChanged, onWsConnect, onWsDisconnect }) {
  useEffect(() => {
    // The 6 conversation lifecycle events (plano 10 FF2) all route to a single
    // onConversationChanged(eventName, data) so the consumer can patch/refetch.
    const conv = onConversationChanged
      ? (name) => (d) => onConversationChanged(name, d)
      : null;
    const ws = createWebSocket({
      onConnect: onWsConnect,
      onDisconnect: onWsDisconnect,
      status: onStatus,
      qr_update: onQrUpdate,
      gowa_status: onGowaStatus,
      config_saved: onConfigSaved,
      new_message: onNewMessage,
      chat_presence: onChatPresence,
      ai_typing: onAiTyping,
      contact_info_updated: onContactInfoUpdated,
      tags_changed: onTagsChanged,
      contact_tags_updated: onContactTagsUpdated,
      human_transfer_alert: onHumanTransferAlert,
      contact_ai_toggled: onContactAiToggled,
      messages_read: onMessagesRead,
      message_status: onMessageStatus,
      message_revoked: onMessageAction ? (d) => onMessageAction({ ...d, action: 'revoked' }) : undefined,
      message_deleted: onMessageAction ? (d) => onMessageAction({ ...d, action: 'deleted' }) : undefined,
      message_reaction: onMessageReaction,
      avatar_updated: onAvatarUpdated,
      group_participants_changed: onGroupParticipantsChanged,
      low_balance: onLowBalance,
      conversation_created: conv ? conv('conversation_created') : undefined,
      conversation_status_changed: conv ? conv('conversation_status_changed') : undefined,
      conversation_assigned: conv ? conv('conversation_assigned') : undefined,
      conversation_archived: conv ? conv('conversation_archived') : undefined,
      conversation_ai_toggled: conv ? conv('conversation_ai_toggled') : undefined,
      conversation_updated: conv ? conv('conversation_updated') : undefined,
    });
    return () => ws.close();
  }, []);
}
