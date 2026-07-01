# WhatsBot Pro — Documentação em Diagramas (Excalidraw)

Diagrama nativo do **Excalidraw** (`.excalidraw`, editável) documentando o
funcionamento do WhatsBot Pro. Gerado com a
[excalidraw-skill](https://github.com/Agents365-ai/excalidraw-skill).

## Arquivos

- **`whatsbot-pro-completo.excalidraw`** — o desenho. Contém os 6 diagramas
  num único canvas, dispostos em 2 colunas.
- `whatsbot-pro-completo.svg` — pré-visualização (renderizada via Kroki) para
  consulta rápida sem abrir o editor.

## Diagramas incluídos

| # | Conteúdo |
|---|---|
| 1 | **Arquitetura geral** — camadas: frontend Preact, backend FastAPI, bridge GOWA, provider Techify e a camada de dados (SQLite/Postgres) |
| 2 | **Fluxo de mensagem** — webhook → batching (~3s) → motor AGNO → LLM → resposta enviada |
| 3 | **Gate de decisão da IA** — cascata: JID permitido → `auto_reply` global → canal `ai_enabled` → conversa `ai_active` → `group_reply_mode` |
| 4 | **Motor AGNO** — delegação do `AgentHandler` ao motor (contexto, tools como `agno.Function`, extração da reply, usage) |
| 5 | **Lifecycle de plugins** — bootstrap → discovery → migrations → import → wiring; e o restart no toggle |
| 6 | **Bus de plugins** — Events (fire-and-forget) vs Filters (síncronos, `None` aborta) |

## Como abrir / editar

1. Abra [excalidraw.com](https://excalidraw.com) (ou o app desktop / extensão VSCode).
2. Menu hambúrguer → **Open** → selecione `whatsbot-pro-completo.excalidraw`.
3. **Shift+1** dá zoom-to-fit pra ver os 6 diagramas de uma vez. Tudo entra como
   formas editáveis (bindings de setas preservados) — ajuste à vontade e salve
   por cima.

## Como regenerar a pré-visualização

O `.svg` é só um preview; a fonte de verdade editável é o `.excalidraw`. Para
regerar o preview (só precisa de `curl`):

```bash
curl -s -X POST https://kroki.io/excalidraw/svg \
  -H "Content-Type: text/plain" \
  --data-binary "@whatsbot-pro-completo.excalidraw" \
  -o whatsbot-pro-completo.svg
```
