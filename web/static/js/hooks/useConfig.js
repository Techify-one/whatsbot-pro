import { useState, useEffect } from 'preact/hooks';
import { getConfig, saveConfig as apiSaveConfig } from '../services/api.js';

export function useConfig() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getConfig().then((res) => {
      if (res.ok) setConfig(res.data);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  async function save(newConfig) {
    setSaving(true);
    try {
      const res = await apiSaveConfig(newConfig);
      if (res.ok) {
        // Reload config to get masked key
        const fresh = await getConfig();
        if (fresh.ok) setConfig(fresh.data);
        return { ok: true, message: res.data?.message || 'Salvo!' };
      }
      return { ok: false, message: res.error || 'Erro ao salvar.' };
    } catch (e) {
      return { ok: false, message: 'Erro de conexão.' };
    } finally {
      setSaving(false);
    }
  }

  return { config, loading, saving, save, setConfig };
}
