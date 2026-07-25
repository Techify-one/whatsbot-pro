// Frontend extension do plugin facebook_messenger (espelha whatsapp_cloud). O app
// importa este módulo UMA vez no boot e chama register(api). Tudo é aditivo contra
// o registry compartilhado do cliente — desabilitar o plugin remove no próximo boot
// e o core renderiza idêntico (a linha de saúde do webhook some do card, sem erro).
//
// Único trabalho: registrar o WebhookHealthRow no slot genérico
// ``channel.card.rows`` (aberto no ChannelCard pelo core). O componente vive no
// plugin e filtra por ``ctx.channel.provider === 'facebook_messenger'``
// internamente, então só aparece nos cards de canal Messenger. O `api.http`
// (transporte status-aware namespaceado em /api/plugins/facebook_messenger) é
// injetado como prop — o componente não importa nada do core api.js.
import { h } from 'preact';
import { WebhookHealthRow } from './WebhookHealthRow.js';

export default function register(api) {
  const http = api.http;
  api.addSlot('channel.card.rows', (props) => h(WebhookHealthRow, { ...props, http }));
}
