import { useEffect } from 'preact/hooks';
import { createWebSocket } from '../services/websocket.js';

export function useWebSocket({ onStatus, onQrUpdate, onGowaStatus, onConfigSaved }) {
  useEffect(() => {
    const ws = createWebSocket({
      status: onStatus,
      qr_update: onQrUpdate,
      gowa_status: onGowaStatus,
      config_saved: onConfigSaved,
    });
    return () => ws.close();
  }, []);
}
