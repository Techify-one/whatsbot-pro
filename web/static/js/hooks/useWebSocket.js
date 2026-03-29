import { useEffect } from 'preact/hooks';
import { createWebSocket } from '../services/websocket.js';

export function useWebSocket({ onStatus, onQrUpdate, onGowaStatus, onConfigSaved, onNewMessage, onChatPresence, onContactInfoUpdated, onTagsChanged, onContactTagsUpdated, onHumanTransferAlert, onContactAiToggled, onMessagesRead }) {
  useEffect(() => {
    const ws = createWebSocket({
      status: onStatus,
      qr_update: onQrUpdate,
      gowa_status: onGowaStatus,
      config_saved: onConfigSaved,
      new_message: onNewMessage,
      chat_presence: onChatPresence,
      contact_info_updated: onContactInfoUpdated,
      tags_changed: onTagsChanged,
      contact_tags_updated: onContactTagsUpdated,
      human_transfer_alert: onHumanTransferAlert,
      contact_ai_toggled: onContactAiToggled,
      messages_read: onMessagesRead,
    });
    return () => ws.close();
  }, []);
}
