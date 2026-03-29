import { useEffect } from 'preact/hooks';
import { createWebSocket } from '../services/websocket.js';

export function useWebSocket({ onStatus, onQrUpdate, onGowaStatus, onConfigSaved, onNewMessage, onChatPresence, onContactInfoUpdated, onTagsChanged, onContactTagsUpdated }) {
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
    });
    return () => ws.close();
  }, []);
}
