// Frontend extension do plugin whatsapp_cloud (plano 76 · F6 + plano 92 · C1). O
// app importa este módulo UMA vez no boot e chama register(api). Tudo é aditivo
// contra o registry compartilhado do cliente — desabilitar o plugin remove no
// próximo boot e o core renderiza sem erro.
//
// Duas reivindicações:
//   1. slot `channel.card.rows` → a linha de saúde do webhook no card do canal.
//      O componente vive no plugin e filtra por
//      ``ctx.channel.provider === 'whatsapp_cloud'`` internamente, então só
//      aparece nos cards de canal Cloud.
//   2. override EXCLUSIVO `template.picker` → o modal "Enviar template". A forma
//      de um template (categorias, `{{n}}`, tipos de botão, aprovação) é ditada
//      pela Graph API, então a tela é deste plugin e não do core (plano 92 · D1).
//
// Em ambos os casos o transporte é injetado como PROP por um wrapper — nenhum
// dos dois componentes importa nada do core.
import { h } from 'preact';
import { WebhookHealthRow } from './WebhookHealthRow.js';
import { TemplatePicker } from './TemplatePicker.js';

export default function register(api) {
  const http = api.http;
  api.addSlot('channel.card.rows', (props) => h(WebhookHealthRow, { ...props, http }));

  // ── template.picker ────────────────────────────────────────────────────────
  // POR ÚLTIMO e sob feature-check. Uma exceção dentro de register() é engolida
  // num console.warn pelo loader, e TUDO que viesse depois da linha que estourou
  // seria perdido em silêncio — então o que pode falhar fica no fim. O
  // `frontend_api_version` do manifest NÃO protege aqui: ele compara só o MAJOR,
  // e `overrideComponent` nasceu dentro do 1.x. O typeof é a única defesa real
  // contra este zip cair num core anterior ao plano 92.
  if (typeof api.overrideComponent === 'function') {
    // `api.services` é a allowlist curada do core — as 10 funções de template
    // passam por ela, e os DOIS uploads (multipart) só funcionam por aqui:
    // `api.http` é JSON-only e transformaria o FormData em "{}".
    // O wrapper é criado UMA vez por register(), então o tipo do vnode é estável
    // entre renders e o modal não se remonta sozinho.
    // `svc` = funções do CORE (listar/enviar/criar/apagar template).
    // `http` = rotas DESTE plugin (/api/plugins/whatsapp_cloud/template-prefs),
    // onde vivem favoritos e arquivados.
    const svc = api.services;
    api.overrideComponent('template.picker',
      (props) => h(TemplatePicker, { ...props, svc, http }));
  }
}
