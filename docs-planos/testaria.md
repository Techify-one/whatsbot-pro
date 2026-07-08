# Plano de teste — IA do WhatsBot (go-live dia 20)

Testar nesta ordem — cada bloco depende do anterior estar funcionando.
Telas de IA: **Configurações → IA** (`GeneralSettings`, `AgentsManager`, `VariablesEditor`, `ToolsUnified`) + config por canal em **Canais**.
Tools core: `save_contact_info`, `set_custom_attribute`, `transfer_to_human`, `transferir_agente`.

---

## BLOCO 0 — Pré-requisitos (destrava tudo)

Se qualquer um falhar, a IA nem chega a responder. Teste primeiro.

- [ ] **WhatsApp conectado** — Canais → GOWA mostra "Conectado/Logado" (QR lido). `GET /api/status` → `connected: true`.
- [ ] **Chave de API válida** — Configurações → IA → testar chave (botão auto-salva se válida). Sem chave verde, nada roda.
- [ ] **Saldo > 0** — badge de saldo / `GET /api/balance`. Recarga aponta pra conta Techify.
- [ ] **Modelo selecionado** — a lista vem de `/api/models` (cache 10 min).

## BLOCO 1 — Os 3 portões da resposta (o teste mais importante) ✅ OK

A IA só responde se os **três** estiverem ligados. Teste isolando cada um:

- [x] **Portão GLOBAL** (`auto_reply`) — Configurações → IA, interruptor mestre. Desligue → mande msg de outro número → **nada** responde. Ligue → responde. _(testado — funciona; o "defeito" reportado foi equívoco)_
- [x] **Portão do CANAL** (`ai_enabled`) — Canais → editar canal → IA. Desligue só o canal → conversa não responde mesmo com global ON.
- [x] **Portão da CONVERSA** (`ai_active`) — dentro de uma conversa, toggle "IA ligada/desligada". Desligue → você atende manual, IA cala.
- [x] **Ponta-a-ponta feliz**: os 3 ON → mande "oi" de outro número → resposta chega no WhatsApp em ~3s.

## BLOCO 2 — Batching, split e contexto ✅ OK (contexto)

- [ ] **Batching** (`message_batch_delay`, padrão 3s) — mande 3 mensagens rápidas seguidas → devem virar **uma** chamada só (resposta única coerente).
- [ ] **Split de mensagens** (`split_messages`) — **fica em Canais → editar canal → IA → "Mensagens picadas (dividir resposta)"** (NÃO em Configurações). Ligue → resposta longa deve chegar quebrada em vários balões.
- [x] **Contexto** (`max_context_messages`) — testado, **passou**. A IA recuperou corretamente o início da conversa dentro da janela.

> **⚠️ Nota importante — janela de contexto × memória de contato (não confundir):**
>
> - `max_context_messages` conta **MENSAGENS, não DIAS**. É uma janela deslizante das últimas N mensagens da conversa. Foi ajustado de **10 → 25** nos dois canais (GOWA + Telegram) direto no banco. _(Cache de 30s: espere ~30s ou salve o canal na UI pra valer.)_
> - Pedir "o que conversamos ontem?" **não** é o que essa feature faz. Se hoje já tem mais de N mensagens, a janela inteira é de hoje e ontem fica de fora — **comportamento correto, não bug**. Alcançar ontem exigiria janela de ~70+ (custo alto).
> - **Memória de longo prazo (entre dias)** = **memória de contato**, não histórico: `save_contact_info` (nome/email/profissão/empresa) + observações, injetadas SEMPRE no system prompt. É o que faz a IA "lembrar do cliente" amanhã. Testado no **Bloco 3**.
> - **Pendente:** colar o trecho de prompt no agente pra ela parar de dizer "não tenho memória / folha em branco / cada conversa começa do zero" (disclaimer genérico que não reflete o produto).

## BLOCO 3 — As 4 tools core (tool calling)

- [ ] **`save_contact_info`** — diga "meu nome é João, email joao@x.com, sou dentista da Clínica Y" → confira que **Nome/Email/Profissão/Empresa** foram salvos no cadastro do contato automaticamente.
- [ ] **`set_custom_attribute`** — peça pra IA registrar um atributo custom → confira no contato.
- [ ] **`transfer_to_human`** — diga "quero falar com um atendente" → IA chama a tool, marca a tag `transferido_atendente` e a IA cala (ver Bloco 6).
- [ ] **`transferir_agente`** — só faz sentido com multi-agente (Bloco 5).
- [ ] **Tela de tools** (`/tools`) — mude `display_label`/`description` de uma tool e confira que aplica; toggle enabled/disabled de uma tool.

## BLOCO 4 — Agentes (config no banco)

- [ ] **Prompt inline** — Configurações → IA → Agentes → edite o prompt de um agente, salve, mande msg → comportamento muda.
- [ ] **Variáveis** (`{placeholder}`) — crie uma `ai_variable`, use `{nome}` no prompt → confira que resolve.
- [ ] **Histórico / Reverter** — salve o agente 2x, abra Histórico, reverta → prompt volta (snapshot cobre o prompt inline).
- [ ] **RBAC do agente** — com um usuário sem permissão de editar/versionar/deletar prompt, confirme que os botões somem/bloqueiam.

## BLOCO 5 — Multi-agente hub-and-spoke (roteamento)

Só se você tiver mais de um agente configurado.

- [ ] **Roteador único** — tente marcar 2 agentes como `is_router` → o segundo desmarca o primeiro (radio + índice único).
- [ ] **Roteamento** — mande msg que pertença a um spoke (ex: "quero comprar") → roteador chama `transferir_agente` → spoke responde. Confira que o **motivo** aparece no fio.
- [ ] **Só o roteador tem `transfer_to_human`** — selecione ela num spoke → deve aparecer o aviso na UI.
- [ ] **Profundidade** (`ai_max_route_depth`, padrão 5) — force vai-e-volta longo → ao estourar, cai em `transfer_to_human` automático ("Limite de roteamento atingido").
- [ ] **Guardrail `requires_prior_call`** — tool que exige outra antes → chame fora de ordem → bloqueio citando `transferir_agente`.
- [ ] **Teto de tool calls** — `ai_tool_call_limit_total` (padrão 25) segura loop.

## BLOCO 6 — Transferência para humano (o gate)

- [ ] IA cala quando: `ai_active=0` **OU** conversa tem `assignee_user_id` humano sem agente ativo **OU** contato tem tag `transferido_atendente`.
- [ ] **Reabrir limpa a tag** — reabra a conversa com IA ligada / toggle-ai enable → a tag `transferido_atendente` some e a IA volta.
- [ ] **Alerta de transferência** — se `transfer_alert_enabled` do canal ON, confira o alerta no painel.

## BLOCO 7 — Mídia / transcrição (chamadas diretas, não-agênticas)

- [ ] **Áudio** — mande um áudio → confira a transcrição no chat (`audio_transcription_mode/_target`).
- [ ] **Imagem** — mande uma foto → descrição gerada (`image_transcription_enabled`).
- [ ] **Documento** — mande um PDF → transcrição (`document_transcription_enabled`).
- [ ] Cada um é per-canal (Canais → IA) — teste ligar/desligar por canal.

## BLOCO 8 — Grupos (@menções)

- [ ] **`group_reply_mode`** (padrão `mention_only`) — em grupo, msg sem @menção → IA **não** responde; com @menção ao bot → responde.
- [ ] **@menção na saída** — peça pra IA "mencione o Fulano" ou "@todos" → vira menção real no WhatsApp.
- [ ] **Nomes** — no painel você vê `@Nome`, não `@número`.

## BLOCO 9 — Avisos de sistema no chat (`conversation_event`)

- [ ] **`ai_takeover`** — 1ª resposta da IA numa conversa gera o card centralizado (1×/conversa).
- [ ] Toggle IA on/off, trocar agente ativo, atribuir/resolver → cada um gera o card (se o grupo de aviso estiver ON em Configurações).

## BLOCO 10 — Análise de melhoria (improvement)

- [ ] Marque uma resposta da IA como **incorreta** (flag do operador) → gera um diagnóstico one-shot (roteador→spoke, prompts crus, tools) salvo como card na conversa.
- [ ] **Prompt de melhoria editável** — Configurações → IA → edite `improvement_prompt` → confirme que o diagnóstico usa o texto novo.

## BLOCO 11 — Observabilidade (confirme que registrou)

- [ ] **Usage** — após várias respostas, Usage mostra tokens/custo por contato e global.
- [ ] **Executions/steps** — cada resposta gera uma execução com passos (tool calls, llm_request) e `total_tokens`/`total_cost_usd`.
- [ ] **Monitor de saldo** — force saldo abaixo do `low_balance_threshold` (US$0,50) → modal de recarga (`low_balance`) abre.
- [ ] **"IA respondendo…"** — enquanto a IA processa, o header mostra o indicador `ai_typing`.

## BLOCO 12 — Onboarding (se for instalar limpo pro cliente)

- [ ] `/wizard` — 3 passos: conectar WhatsApp → provisionar chave (manda msg ao número Techify, credita ~US$1) → escrever prompt do agente. Só aparece em instalação nova.

---

**Ordem prática pra hoje:** Bloco 0 → 1 → 2 → 3 (valida 80% do fluxo real). Depois 6 e 7 (transferência + mídia). Multi-agente (5) e improvement (10) por último, se você usa esses recursos.
