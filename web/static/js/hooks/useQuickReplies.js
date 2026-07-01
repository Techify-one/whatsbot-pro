import { useState, useEffect } from 'preact/hooks';
import { getQuickReplies } from '../services/api.js';

// Hook das respostas rápidas (plano 04): carrega a lista global e recarrega quando
// a tela de gerenciamento emite `whatsbot:quick-replies-changed`. Expõe
// `getCandidates(query)` para o autocomplete do "/" no compositor (espelha a lógica
// inline do ContactDetail, agora reutilizável — usado também na Novo atendimento).
export function useQuickReplies() {
  const [quickReplies, setQuickReplies] = useState([]);

  useEffect(() => {
    let alive = true;
    function load() {
      getQuickReplies().then(res => { if (alive && res && res.ok) setQuickReplies(res.data || []); });
    }
    load();
    window.addEventListener('whatsbot:quick-replies-changed', load);
    return () => { alive = false; window.removeEventListener('whatsbot:quick-replies-changed', load); };
  }, []);

  // Casa por short_code OU conteúdo (case-insensitive), no máximo 8 sugestões.
  function getCandidates(query) {
    const q = (query || '').toLowerCase();
    return quickReplies.filter(qr =>
      qr.short_code.toLowerCase().includes(q) || (qr.content || '').toLowerCase().includes(q)
    ).slice(0, 8);
  }

  return { quickReplies, getCandidates };
}
