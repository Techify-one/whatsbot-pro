// @ts-check
//
// Token-autocomplete hook (Plano 23 · D3) — extracted verbatim from
// ContactDetail.js. Owns BOTH composer menus:
//   • @mention (group chats only): fetches participants, detects an "@token" at
//     the caret, offers candidates (incl. the special "todos" → @todos), and
//     replaces the token with "@Name " on pick. The server later resolves the
//     @name → @number when sending.
//   • /quick-reply (any chat): loads the global list, detects a "/token", and
//     inserts the snippet literally (NOT sent).
//
// Pure parsing lives in services/composerTokens.js (caret/token detection +
// candidate filtering); this hook keeps the state, the fetch effects, the
// caret-aware insertion, and the keyboard navigation. Behavior-preserving: same
// regexes, same debounce-free open/close, same member-roster live update.
import { useState, useEffect, useRef } from 'preact/hooks';
import { getGroupMembers, getQuickReplies, getAssignableAgents } from '../../../services/api.js';
import {
  detectMentionToken, detectQuickReplyToken, replaceToken,
  mentionLabel, mentionCandidates, quickReplyCandidates,
} from '../../../services/composerTokens.js';

/**
 * @param {Object} opts
 * @param {string} opts.phone
 * @param {boolean} opts.sandbox
 * @param {any} opts.contact
 * @param {any} opts.groupParticipantsChanged - WS roster-change payload | null.
 * @param {string} opts.input
 * @param {(v:string)=>void} opts.setInput
 * @param {{ current: HTMLTextAreaElement|null }} opts.inputRef
 * @param {boolean} [opts.mentionsUnsupported] - o destino atual do texto não aceita menção.
 */
export function useTokenAutocomplete({
  phone, sandbox, contact, groupParticipantsChanged, input, setInput, inputRef,
  mode = 'reply', mentionsUnsupported = false,
}) {
  // Group @mention autocomplete: list of participants + open menu state.
  const [members, setMembers] = useState([]);
  // @mention INTERNA (nota privada, estilo Chatwoot): atendentes do painel +
  // um item "Time (caixa)". Fonte = /assignable-agents. As escolhas são rastreadas
  // por rótulo → user_id (picksRef) e o flag de time, lidos no envio (collectMentions).
  const [internalAgents, setInternalAgents] = useState([]);
  const picksRef = useRef(new Map());   // '@Nome' inserido -> user_id
  const teamPickedRef = useRef(false);  // "@Time" escolhido?
  const isPrivate = mode === 'private';
  // mentionMenu: { query, start (index of '@' in input), index (highlighted) } | null
  const [mentionMenu, setMentionMenu] = useState(null);
  // Quick replies (plano 04): global list loaded once + the "/atalho" menu.
  // quickReplyMenu: { query, start (index of '/' in input), index (highlighted) } | null
  const [quickReplies, setQuickReplies] = useState([]);
  const [quickReplyMenu, setQuickReplyMenu] = useState(null);

  const isGroup = !!(contact && contact.is_group);

  // Fetch group participants for @mention autocomplete.
  useEffect(() => {
    setMembers([]);
    setMentionMenu(null);
    if (!phone || sandbox || !(contact && contact.is_group)) return;
    let cancelled = false;
    getGroupMembers(phone)
      .then(res => { if (!cancelled && res && res.ok) setMembers(res.data.members || []); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [phone, contact && contact.is_group]);

  // Fetch internal agents (painel) for private-note @mention autocomplete. Cheap,
  // loaded once when the private composer is first used.
  useEffect(() => {
    if (sandbox || !isPrivate || internalAgents.length) return;
    let cancelled = false;
    getAssignableAgents()
      .then(res => { if (!cancelled && res && res.ok) setInternalAgents(res.data.users || []); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [isPrivate, sandbox]);

  // Trocar de conversa zera as escolhas de menção pendentes (não vazam para outra thread).
  useEffect(() => {
    picksRef.current = new Map();
    teamPickedRef.current = false;
  }, [phone]);

  // A member joined/left the OPEN group. The server already applied the delta
  // (added member with its push name, or dropped a removed one) and ships the
  // authoritative roster in the event — use it directly so a removed member
  // disappears at once and a just-joined one shows its name. Fall back to a
  // forced refetch only if the event somehow carries no member list.
  useEffect(() => {
    if (!groupParticipantsChanged || sandbox) return;
    if (!phone || !(contact && contact.is_group)) return;
    if (groupParticipantsChanged.group_jid !== phone) return;
    if (Array.isArray(groupParticipantsChanged.members)) {
      setMembers(groupParticipantsChanged.members);
      return;
    }
    let cancelled = false;
    getGroupMembers(phone, true)
      .then(res => { if (!cancelled && res && res.ok) setMembers(res.data.members || []); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [groupParticipantsChanged]);

  // ── Quick replies (plano 04): load the global list once, refresh on change ──
  useEffect(() => {
    let alive = true;
    function load() {
      getQuickReplies().then(res => { if (alive && res && res.ok) setQuickReplies(res.data || []); });
    }
    load();
    window.addEventListener('whatsbot:quick-replies-changed', load);
    return () => { alive = false; window.removeEventListener('whatsbot:quick-replies-changed', load); };
  }, []);

  // Candidate getters (pure parsing delegated to composerTokens).
  function getMentionCandidates(query) {
    // Nota privada: atendentes internos + "Time (caixa)" (≠ participantes do grupo).
    if (isPrivate) {
      const q = (query || '').toLowerCase();
      const list = [];
      if (!q || 'time'.startsWith(q) || 'caixa'.startsWith(q) || 'equipe'.startsWith(q)) {
        list.push({ team: true, name: 'Time (caixa)' });
      }
      for (const a of internalAgents) {
        const name = a.name || a.email || '';
        if (name.toLowerCase().includes(q)) {
          list.push({ internal: true, user_id: a.id, name, is_admin: a.is_admin });
        }
      }
      return list.slice(0, 8);
    }
    return mentionCandidates(query, members);
  }
  function getQuickReplyCandidates(query) {
    return quickReplyCandidates(query, quickReplies);
  }

  // Detect an "@token" at the cursor and open/close the mention menu. Habilitado
  // em grupos (menção de participante) OU no modo privado (menção de atendente/time).
  //
  // ⚠️ Plano 124 — com anexo na bandeja, o texto do compositor é a LEGENDA. As
  // rotas de mídia para o cliente (`/send-image` e irmãs) não recebem `mentions`,
  // então um "@Fulano" ali sairia como texto literal. O menu é suprimido nesse
  // caso para não prometer o que o envio não entrega. A NOTA PRIVADA é a
  // exceção: `/private-image` e `/private-document` aceitam menções, e o
  // `useMediaUpload` passa a mandá-las.
  function updateMentionMenu(el, val) {
    if (sandbox || !(isPrivate || (contact && contact.is_group))) { setMentionMenu(null); return; }
    if (mentionsUnsupported) { setMentionMenu(null); return; }
    const pos = (el && el.selectionStart != null) ? el.selectionStart : val.length;
    const tok = detectMentionToken(val.slice(0, pos), pos);
    if (tok) setMentionMenu({ query: tok.query, start: tok.start, index: 0 });
    else setMentionMenu(null);
  }

  // Replace the typed "@token" with the chosen mention and close the menu.
  function applyMention(cand) {
    if (!cand || !mentionMenu) return;
    const el = inputRef.current;
    const pos = (el && el.selectionStart != null) ? el.selectionStart : input.length;
    // Rótulo inserido no texto. No modo privado rastreamos a escolha (rótulo → user_id
    // ou flag de time) para o envio saber os destinatários.
    let label;
    if (isPrivate) {
      if (cand.team) { label = 'Time'; teamPickedRef.current = true; }
      else { label = cand.name || ''; if (cand.user_id != null) picksRef.current.set(label, cand.user_id); }
    } else {
      label = cand.special ? 'todos' : mentionLabel(cand);
    }
    const { value: newVal, caret } = replaceToken(input, mentionMenu.start, pos, '@' + label + ' ');
    setInput(newVal);
    setMentionMenu(null);
    setTimeout(() => {
      if (el) {
        el.focus();
        el.setSelectionRange(caret, caret);
      }
    }, 0);
  }

  // No envio: dado o texto final, resolve os destinatários realmente presentes (o
  // operador pode ter apagado um "@Nome"). Zera após enviar via resetMentions.
  function collectMentions(text) {
    const t = text || '';
    const ids = [];
    for (const [label, uid] of picksRef.current.entries()) {
      if (t.includes('@' + label)) ids.push(uid);
    }
    const mention_inbox = !!(teamPickedRef.current && t.includes('@Time'));
    return { mentions: Array.from(new Set(ids)), mention_inbox };
  }
  function resetMentions() {
    picksRef.current = new Map();
    teamPickedRef.current = false;
  }

  // Detect a "/token" at the cursor and open/close the quick-reply menu. Unlike
  // @mention this works in ANY conversation; opens only when there are matches
  // (so plain messages starting with "/" — e.g. URLs — aren't hijacked).
  function updateQuickReplyMenu(el, val) {
    if (sandbox) { setQuickReplyMenu(null); return; }
    const pos = (el && el.selectionStart != null) ? el.selectionStart : val.length;
    const tok = detectQuickReplyToken(val.slice(0, pos), pos);
    if (tok && getQuickReplyCandidates(tok.query).length) {
      setQuickReplyMenu({ query: tok.query, start: tok.start, index: 0 });
    } else {
      setQuickReplyMenu(null);
    }
  }

  // Replace the typed "/token" with the chosen content (literal, NOT sent).
  function applyQuickReply(cand) {
    if (!cand || !quickReplyMenu) return;
    const el = inputRef.current;
    const pos = (el && el.selectionStart != null) ? el.selectionStart : input.length;
    const { value: newVal, caret } = replaceToken(input, quickReplyMenu.start, pos, cand.content);
    setInput(newVal);
    setQuickReplyMenu(null);
    setTimeout(() => {
      if (el) {
        el.focus();
        el.setSelectionRange(caret, caret);
      }
    }, 0);
  }

  // Update both menus from an input event. Returns nothing; called by the composer.
  function updateMenus(el, val) {
    updateMentionMenu(el, val);
    updateQuickReplyMenu(el, val);
  }

  // Keyboard navigation for the open menu. Returns true when the key was handled
  // (the composer must then NOT fall through to send). Mirrors the original
  // handleKeyDown branches; mention and quick-reply are mutually exclusive.
  function handleMenuKeyDown(e) {
    if (mentionMenu) {
      const cands = getMentionCandidates(mentionMenu.query);
      if (cands.length) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setMentionMenu(mm => ({ ...mm, index: Math.min((mm.index || 0) + 1, cands.length - 1) }));
          return true;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setMentionMenu(mm => ({ ...mm, index: Math.max((mm.index || 0) - 1, 0) }));
          return true;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          applyMention(cands[Math.min(mentionMenu.index || 0, cands.length - 1)]);
          return true;
        }
      }
      if (e.key === 'Escape') { e.preventDefault(); setMentionMenu(null); return true; }
    }
    if (quickReplyMenu) {
      const cands = getQuickReplyCandidates(quickReplyMenu.query);
      if (cands.length) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setQuickReplyMenu(mm => ({ ...mm, index: Math.min((mm.index || 0) + 1, cands.length - 1) }));
          return true;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setQuickReplyMenu(mm => ({ ...mm, index: Math.max((mm.index || 0) - 1, 0) }));
          return true;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          applyQuickReply(cands[Math.min(quickReplyMenu.index || 0, cands.length - 1)]);
          return true;
        }
      }
      if (e.key === 'Escape') { e.preventDefault(); setQuickReplyMenu(null); return true; }
    }
    return false;
  }

  return {
    members, isGroup,
    mentionMenu, setMentionMenu, quickReplyMenu, setQuickReplyMenu,
    getMentionCandidates, getQuickReplyCandidates, mentionLabel,
    updateMenus, applyMention, applyQuickReply, handleMenuKeyDown,
    collectMentions, resetMentions,
  };
}
