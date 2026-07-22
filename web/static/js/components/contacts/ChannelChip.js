import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

// Selo do CANAL da conversa (WhatsApp / Telegram / Cloud API …), com um pontinho
// e o nome do canal. Um só componente para as duas pontas que o mostram — a linha
// da barra lateral e o cabeçalho do chat aberto — para que nunca divirjam.
//
// O rótulo preferido é o NOME do canal (como o operador batizou na tela Canais);
// `CHANNEL_META` só entra como fallback e para a cor. Provider desconhecido
// (plugin novo) degrada para o próprio identificador em cinza — nunca some.
export const CHANNEL_META = {
  gowa:           { label: 'WhatsApp',  cls: 'bg-wa-teal/15 text-wa-teal' },
  whatsapp_cloud: { label: 'Cloud API', cls: 'bg-blue-100 text-blue-700' },
  telegram:       { label: 'Telegram',  cls: 'bg-blue-100 text-blue-700' },
  test:           { label: 'Teste',     cls: 'bg-wa-hover text-wa-secondary' },
};

export function channelMetaFor(provider) {
  return CHANNEL_META[provider] || { label: provider, cls: 'bg-wa-hover text-wa-secondary' };
}

// `margin`: a linha da sidebar separa os selos por margem própria (ml-[6px]), mas o
// cabeçalho do chat já é um flex com `gap` — lá o espaçamento sai desligado para não
// somar duas vezes. O visual do selo em si é idêntico nos dois.
export function ChannelChip({ provider, name, margin = true }) {
  if (!provider) return null;
  const meta = channelMetaFor(provider);
  return html`<span
    class="${margin ? 'ml-[6px] ' : ''}inline-flex items-center gap-[3px] text-[10px] font-semibold rounded px-[5px] py-[1px] align-middle shrink-0 ${meta.cls}"
    title=${name ? `Canal: ${name} (${provider})` : `Canal: ${provider}`}
  ><span class="w-[5px] h-[5px] rounded-full bg-current opacity-70"></span>${name || meta.label}</span>`;
}

export default ChannelChip;
