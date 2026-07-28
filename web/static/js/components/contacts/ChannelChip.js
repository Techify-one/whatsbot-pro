import { h } from 'preact';
import htm from 'htm';
import { providerLabel, providerTint } from '../../services/providerCatalog.js';
import { useProviderCatalog } from '../../hooks/useProviderCatalog.js';

const html = htm.bind(h);

// Selo do CANAL da conversa (WhatsApp / Telegram / Cloud API …), com um pontinho
// e o nome do canal. Um só componente para as duas pontas que o mostram — a linha
// da barra lateral e o cabeçalho do chat aberto — para que nunca divirjam.
//
// O rótulo preferido é o NOME do canal (como o operador batizou na tela Canais);
// o rótulo/cor do provider vêm do CATÁLOGO ÚNICO (plano 76 · providerCatalog),
// alimentado pelo descriptor — não há mais mapa estático aqui. Provider
// desconhecido (plugin desinstalado) degrada para o próprio id em cinza.
export function channelMetaFor(provider) {
  return { label: providerLabel(provider), cls: providerTint(provider) };
}

// `margin`: a linha da sidebar separa os selos por margem própria (ml-[6px]), mas o
// cabeçalho do chat já é um flex com `gap` — lá o espaçamento sai desligado para não
// somar duas vezes. O visual do selo em si é idêntico nos dois.
export function ChannelChip({ provider, name, margin = true }) {
  useProviderCatalog();  // re-render quando os descriptors chegarem (async)
  if (!provider) return null;
  const meta = channelMetaFor(provider);
  return html`<span
    class="${margin ? 'ml-[6px] ' : ''}inline-flex items-center gap-[3px] text-[10px] font-semibold rounded px-[5px] py-[1px] align-middle shrink-0 ${meta.cls}"
    title=${name ? `Canal: ${name} (${provider})` : `Canal: ${provider}`}
  ><span class="w-[5px] h-[5px] rounded-full bg-current opacity-70"></span>${name || meta.label}</span>`;
}

export default ChannelChip;
