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
import { useState, useEffect } from 'preact/hooks';
import { getGroupMembers, getQuickReplies } from '../../../services/api.js';
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
 */
export function useTokenAutocomplete({
  phone, sandbox, contact, groupParticipantsChanged, input, setInput, inputRef,
}) {
  // Group @mention autocomplete: list of participants + open menu state.
  const [members, setMembers] = useState([]);
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
    return mentionCandidates(query, members);
  }
  function getQuickReplyCandidates(query) {
    return quickReplyCandidates(query, quickReplies);
  }

  // Detect an "@token" at the cursor and open/close the mention menu.
  function updateMentionMenu(el, val) {
    if (sandbox || !(contact && contact.is_group)) { setMentionMenu(null); return; }
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
    const label = cand.special ? 'todos' : mentionLabel(cand);
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
  };
}
