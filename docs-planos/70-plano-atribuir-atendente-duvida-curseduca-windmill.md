# Plano 70 — [SUPERSEDIDO → ver Plano 71] Dúvida Curseduca atribuída via Windmill (Opção A, descartada)

> **Status:** ⛔ SUPERSEDIDO · **Data:** 2026-07-21
> **Substituído por:** [71-plano-atendente-padrao-por-canal.md](71-plano-atendente-padrao-por-canal.md).
> Este plano descrevia a **Opção A**: fazer a dúvida do fórum Curseduca nascer atribuída ao **Atendente X** estendendo o **script do Windmill** (`f/whatsbot/duvidas_forum_curseduca_prod`) para se autenticar como um **usuário-operador** (login → token de 30 dias, cache, re-login em 401), resolver o atendente, achar a conversa por poll e chamar `assign-agent`. **Zero código no WhatsBot**, mas exigia um **usuário-robô + senha** guardada como secret do Windmill + poll do `conv_id`.

## Por que foi descartado

Após a investigação (nesta sessão), o usuário escolheu a **Opção B** (2026-07-21): um recurso genérico de **core** — "atendente padrão para novas conversas" por canal — que carimba o `assignee_user_id` + IA off **no nascimento da conversa**, server-side. Isso:

- elimina o **usuário-robô + senha** e o **poll** da Opção A;
- **não muda nada no Windmill** (o script de produção fica intocado);
- é **genérico e reusável** (qualquer canal), com o canal "Avisos Curseduca" como 1º consumidor (→ Atendente X, `user_id 5`).

## Fatos de investigação reaproveitados no Plano 71

- Canal alvo: `channel_id = website_54146c91`, `inbox_id = 20`, widget `wgt_0ad4HfbwTJ`; Atendente X `user_id = 5` (ativo); `phone` do contato website = `wsess_…`.
- Endpoints de operador (mapeados, **não usados** na Opção B): `POST /api/auth/login` (TTL 30 dias), `GET /api/atendimentos/assignable-agents`, `GET /api/contacts/{phone}/atendimento`, `POST /api/atendimentos/{conv_id}/assign-agent {kind:"user"}`.
- Rascunho do script da Opção A (arquivado, **não usado**): `scratchpad/duvidas_forum_curseduca_prod_v2.py`.

**A automação Curseduca não usa Chatwoot nem n8n** (é Windmill → endpoints públicos do WhatsBot); a solução escolhida (Plano 71) é puro core do WhatsBot, sem tocar em Chatwoot/n8n.

➡️ **Toda a implementação está no [Plano 71](71-plano-atendente-padrao-por-canal.md).**
