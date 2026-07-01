// @ts-check
//
// New-conversation / channel-picker hook (Plano 23 · D2) — extracted verbatim
// from Contacts.js. Owns the "start conversation" flow: phone validity check,
// the multichannel inbox picker popup, opening a thread scoped to the chosen
// channel (resolving the existing conversation or seeding `newConvChannelRef`
// for an empty thread), and the "Iniciar atendimento" modal toggle + its error
// state.
//
// Cross-hook wiring: `selectContact` + `fetchContacts` (open/refresh the thread),
// `setSearch` (cleared on open), and the container-owned `newConvChannelRef`
// (read-once by the detail loader) are passed in.
import { useState, useCallback } from 'preact/hooks';
import { checkPhone, listConnectedChannels, getChannelSessionState } from '../../../services/api.js';
import { formatPhoneDisplay } from '../../../utils/phone.js';

/**
 * @param {Object} opts
 * @param {(rowOrPhone:any, msgId?:any)=>void} opts.selectContact
 * @param {(q?:string)=>void} opts.fetchContacts
 * @param {(v:string)=>void} opts.setSearch
 * @param {{ current: string|null }} opts.newConvChannelRef
 */
export function useChannelPicker({ selectContact, fetchContacts, setSearch, newConvChannelRef }) {
  const [checkingPhone, setCheckingPhone] = useState(false);
  const [checkPhoneError, setCheckPhoneError] = useState(null);
  // Popup de escolha de caixa de entrada ao iniciar um atendimento novo (multicanal).
  const [channelPicker, setChannelPicker] = useState(null);  // {phone, phoneDisplay, channels} | null
  // Modal "Iniciar atendimento" (compor número + canal + 1ª mensagem) — menu da engrenagem.
  const [showNewConversation, setShowNewConversation] = useState(false);

  const handleStartConversation = useCallback(async (normalizedPhone) => {
    if (!normalizedPhone || checkingPhone) return;

    setCheckingPhone(true);
    setCheckPhoneError(null);

    try {
      const res = await checkPhone(normalizedPhone);
      if (!res.ok) {
        setCheckPhoneError(res.error || 'Erro ao verificar número.');
        setCheckingPhone(false);
        return;
      }

      if (!res.data.registered) {
        setCheckPhoneError('Este número não possui WhatsApp.');
        setCheckingPhone(false);
        return;
      }

      // Number is valid — use canonical phone from API (avoids BR duplicates)
      const canonicalPhone = res.data.phone || normalizedPhone;

      // Multicanal: deixar o operador escolher por qual caixa de entrada CONECTADA
      // iniciar o atendimento. O backend já filtra desconectadas/desabilitadas.
      const chRes = await listConnectedChannels();
      const channels = (chRes && chRes.ok && Array.isArray(chRes.data)) ? chRes.data : [];
      setCheckingPhone(false);
      setCheckPhoneError(null);

      if (channels.length === 0) {
        setCheckPhoneError('Nenhuma caixa de entrada conectada para iniciar o atendimento.');
        return;
      }

      setChannelPicker({
        phone: canonicalPhone,
        // Exibir o número como o operador digitou (preserva o 9º dígito e o
        // DDD com/sem zero à esquerda). O `canonicalPhone` normalizado pelo
        // backend pode soltar o 9º dígito em celulares BR, o que quebra o
        // slice do formatPhoneDisplay (ex.: +55 (64) 91111-001). O roteamento
        // continua usando `canonicalPhone`; só o rótulo usa o que foi digitado.
        phoneDisplay: formatPhoneDisplay(normalizedPhone),
        channels,
      });
    } catch (e) {
      setCheckPhoneError('Erro ao verificar número. Tente novamente.');
      setCheckingPhone(false);
    }
  }, [checkingPhone]);

  // Caixa de entrada escolhida no popup — abre a thread DAQUELE canal. Resolve a
  // atendimento existente do contato NAQUELE canal (multicanal): se já houver, abre-a
  // escopada (não cai no atendimento de outro canal do mesmo número); se não houver,
  // abre um thread vazio do canal escolhido (a 1ª mensagem é roteada por ele).
  const openInChannel = useCallback(async (phone, channelId) => {
    setSearch('');
    let convId = null;
    try {
      const ss = await getChannelSessionState(channelId, phone);
      if (ss && ss.ok && ss.data && ss.data.conversation_id) convId = ss.data.conversation_id;
    } catch (e) { /* best-effort: cai no thread vazio do canal */ }
    // Sem atendimento ainda nesse canal: marca o canal para o loader escopar o
    // getContact (senão ele funde os canais e mostra o atendimento errada).
    newConvChannelRef.current = convId == null ? channelId : null;
    selectContact({
      phone,
      conversation_id: convId,
      channel_id: channelId,
      contact_id: null,
      id: null,
    });
    fetchContacts();
  }, [selectContact, fetchContacts, setSearch, newConvChannelRef]);

  const handlePickChannel = useCallback((channel) => {
    const picker = channelPicker;
    if (!picker) return;
    setChannelPicker(null);
    openInChannel(picker.phone, channel.id);
  }, [channelPicker, openInChannel]);

  // 1ª mensagem enviada pelo modal "Iniciar atendimento" — fecha o modal, recarrega a
  // sidebar (o atendimento novo já aparece) e abre a thread vinculada ao canal usado.
  const handleNewConversationSent = useCallback((phone, channelId) => {
    setShowNewConversation(false);
    openInChannel(phone, channelId);
  }, [openInChannel]);

  return {
    checkingPhone, checkPhoneError, setCheckPhoneError,
    channelPicker, setChannelPicker,
    showNewConversation, setShowNewConversation,
    handleStartConversation, openInChannel, handlePickChannel, handleNewConversationSent,
  };
}
