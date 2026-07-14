# Plano 51 — Página de avaliação (Cloudflare Worker)

> Fase 2 do plano [50](50-plano-plugin-avaliacao-protocolo.md). O plano 50 fez o
> lado WhatsBot (plugin `protocolos`: persiste o `id_protocol` no fechamento e expõe
> as rotas públicas). Este plano é **só a página externa** que o cliente abre.

## Objetivo

Página pública que o cliente abre pelo **link enviado no fechamento do protocolo**.
Ela: (1) mostra o **atendente** + **código do protocolo**, (2) coleta **nota 1–5** +
**sugestão/observação**, (3) envia de volta ao WhatsBot. Roda num **Cloudflare Worker**.

## Contrato de integração (vem do plano 50 — já implementado)

- **Link enviado ao cliente**: `<worker-url>?id_protocol=<código>&assignee_id=<id>`
  (o plugin anexa os params sozinho; o operador só cola a URL do Worker na aba
  *Avaliação* do plugin protocolos).
- **Endpoints do WhatsBot** (públicos, sem auth):
  - `GET  /api/plugins/protocolos/public/avaliacao/{id_protocol}`
    → `{ ok, data: { atendente, protocolo, ja_avaliado, nota } }` | 404
  - `POST /api/plugins/protocolos/public/avaliacao/{id_protocol}` body `{ nota:1..5, sugestao }`
    → `{ ok:true }` | 400 (nota inválida) | 404 | 409 (já avaliado)
- **URL do WhatsBot (por enquanto)**: `https://whatsbot-dev.teste.techify.run`

## Decisões / restrições (do usuário)

| Item | Decisão |
|---|---|
| Domínio | **Sem domínio custom** — usar o padrão `*.workers.dev` da conta |
| Conta Cloudflare | API token já configurado (`CLOUDFLARE_API_TOKEN`) → autenticado como **`contato.exemplo@example.com`** ⚠️ (usuário citou `contato2.exemplo@example.com` — **confirmar**) |
| Pasta do projeto | **`/home/thiago/whatsbot-avaliacao/`** (criada) |
| WhatsBot URL | `https://whatsbot-dev.teste.techify.run` (provisória, via env var do Worker) |
| Stack | Cloudflare Worker, **JS puro (ES module), sem build step** |

## Arquitetura — Worker que serve a página + proxeia a API

O Worker faz **tudo** (não há CORS, e a URL do WhatsBot nunca vai ao browser):

- **`GET /` (ou `/avaliacao`) com `?id_protocol=…`** →
  1. extrai `id_protocol` da query
  2. **fetch server-side** `{WHATSBOT_URL}/api/plugins/protocolos/public/avaliacao/{id}`
  3. **404** → renderiza página "link inválido/expirado"
  4. **`ja_avaliado=true`** → renderiza "você já avaliou · obrigado" (mostra a nota dada)
  5. **ok** → renderiza a página de avaliação com `atendente` + `protocolo` **embutidos no HTML** (SSR — sem round-trip extra no browser). HTML/CSS/JS **inline, self-contained**.
- **`POST /responder`** body `{ id_protocol, nota, sugestao }` →
  1. valida no Worker (`nota ∈ 1..5`, tamanho da sugestão) — defesa em profundidade
  2. **proxy** `POST {WHATSBOT_URL}/api/plugins/protocolos/public/avaliacao/{id}` com `{nota, sugestao}`
  3. repassa `{ok}` / erro (409 já avaliado, 404, 400) ao front
- **Segurança**: `WHATSBOT_URL` só no Worker (env var); nada de credencial no browser.
  **Escapar HTML** dos valores injetados (`atendente`, `protocolo`) → sem XSS. Rate-limit
  e uso único já existem no WhatsBot (plano 50).

## Fluxo / UX

Mobile-first (é aberto no celular do cliente). Espelha as telas de referência (estilo
conversa): **a definir no passo de design — ver "Pendências"**. Estados cobertos:

1. **Boas-vindas** — código do protocolo + "Atendente: `<nome>`".
2. **Nota** — "Dê uma nota para seu atendimento" + botões `1-Péssimo … 5-Ótimo`.
3. **Sugestão** — após escolher a nota: "Alguma sugestão ou ponto de melhoria?" → **Sim** (abre textarea) / **Não** (envia direto).
4. **Enviar** → tela de **agradecimento**.
5. **Estados de borda**: já avaliado, link inválido/expirado (404), erro de rede (com *retry*), nota fora de 1–5.

## Estrutura do projeto

```
/home/thiago/whatsbot-avaliacao/
├── wrangler.jsonc     — name, main, compatibility_date, workers_dev:true, vars.WHATSBOT_URL
├── src/index.js       — Worker (fetch): SSR do GET + proxy do POST + templates HTML inline
├── package.json       — devDep wrangler + scripts: dev / deploy
├── .gitignore         — node_modules, .wrangler, .dev.vars
└── README.md          — como rodar/deployar + como plugar no protocolos
```

`wrangler.jsonc` (essência):
```jsonc
{
  "name": "whatsbot-avaliacao",
  "main": "src/index.js",
  "compatibility_date": "2025-07-01",
  "workers_dev": true,
  "vars": { "WHATSBOT_URL": "https://whatsbot-dev.teste.techify.run" }
}
```

## Deploy

1. `cd /home/thiago/whatsbot-avaliacao && npm install`
2. `npx wrangler deploy` (usa o `CLOUDFLARE_API_TOKEN` já presente → não-interativo)
3. URL final: **`whatsbot-avaliacao.<subdomínio-da-conta>.workers.dev`** (confirmada na saída do deploy)
4. Colar essa URL no link **normal** da aba *Avaliação* do plugin protocolos
   (Gerenciar Plugins → protocolos → Configurar → Avaliação). O plugin anexa
   `?id_protocol=…&assignee_id=…` automaticamente.

Trocar a URL do WhatsBot depois (produção) = editar `vars.WHATSBOT_URL` e re-deployar
(ou `wrangler secret`/env do dashboard).

## Testes / verificação

- **Local**: `npx wrangler dev` + abrir `http://localhost:8787/?id_protocol=<código-de-teste>`
  com `WHATSBOT_URL` apontando pro WhatsBot de teste. Semear um token pelo fechamento de
  um protocolo real (ou pela função `register_avaliacao`).
- **Round-trip real**: fechar um protocolo no WhatsBot de teste → abrir o link → avaliar →
  conferir a nota **no protocolo** (Kanban/detalhe) e via `GET …/avaliacao/{id}`.
- **Bordas**: id inexistente → página de link inválido (404); reenvio → "já avaliado" (409);
  nota fora de 1–5 rejeitada.

## Fora de escopo

- Domínio custom (fica no `workers.dev` por enquanto).
- Branding avançado / logo / i18n (só PT-BR).
- Persistência/analytics no Worker (a fonte da verdade é o WhatsBot).

## Pendências (decidir antes de codar)

1. **Estilo visual** — réplica "estilo WhatsApp/conversa" (como nas telas de referência) **ou** um cartão limpo e branded. Muda a implementação do HTML/CSS.
2. **Confirmar a conta** Cloudflare (`contato.exemplo@example.com` × `contato2.exemplo@example.com`).
3. **Nome/slug** do Worker (`whatsbot-avaliacao`) → define a URL `*.workers.dev`.
