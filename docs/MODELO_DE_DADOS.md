# Modelo de dados — notas de coluna

Apêndice do `CLAUDE.md` (plano 139). Lá ficou a lista das 20 tabelas em uma
linha por tabela; aqui ficam as **notas de coluna completas**, que são o que
uma sessão futura precisa quando vai escrever query ou migration — em especial
as de `messages`, onde a diferença entre `content` e `media_caption` já custou
um bug de vazamento (plano 133).

A fonte de verdade do schema continua sendo [db/tables.py](../db/tables.py) e as
migrations em [db/alembic/versions](../db/alembic/versions). Este arquivo explica
o QUE cada coluna significa e por que existe — o que o DDL não conta.

> A análise de negócio do banco (dicionário para IA analista, receitas de SQL,
> instrumentação recomendada) mora em `docs/analises/`, **fora do versionamento**:
> descreve regras internas com detalhe suficiente para virar mapa de ataque e
> este repositório é público. Existe só no checkout local.

## Tabelas — descrição completa


| Tabela | Descrição |
|--------|-----------|
| `config` | Configurações do app (key-value, valores JSON-encoded). Configs de plugin usam prefixo `plugin.<id>.` |
| `contacts` | Contatos/grupos (phone, name, email, profissão, empresa, flags). Inclui `is_pinned` (fixar conversa no topo), `has_unread_mention` (@menção não lida em grupo) e `contact_type` (tipo herdado do canal de origem — `whatsapp`/`telegram`/`outros`; ver "Tipo de contato por canal") |
| `observations` | Notas/observações por contato (texto livre) |
| `messages` | Histórico completo de mensagens (role, content, ts, media). Inclui `revoked` (apagada pra todos), `reactions` (JSON `{emoji: [reactor,...]}`), `reply_to_msg_id` (msg_id GOWA da mensagem citada), `edited_ts` (epoch da última edição de uma msg de saída; NULL = nunca editada → o painel mostra "editada") e `media_caption` (plano 87 — a legenda que o cliente digitou junto da mídia, VERBATIM, gravada no INSERT; existe porque `content` é COMPOSTO: a descrição de imagem / extração de documento o reescreve para `"[Descrição da imagem]: <desc>\n<legenda>"` e o painel não consegue separar os dois de forma confiável. NULL = mídia sem legenda ou linha legada → o painel cai no fallback por prefixo). Roles especiais painel-only (não vão ao WhatsApp, renderizam como card centralizado): `tool_call`, `system_notice`, `transcription`, `private_note`, `error`, `conversation_event` (avisos de ciclo de vida da conversa — plano 12) |
| `usage` | Registros de uso da API (tokens, custo, modelo) |
| `tags` | Tags globais (name, color) |
| `contact_tags` | Relação N:N contato ↔ tag |
| `unread_msg_ids` | IDs de mensagens não lidas por contato |
| `executions` | Tracking de execuções (webhook → resposta). Inclui `agent_key`, `total_tokens`, `total_cost_usd` (populados pelo writer a cada chamada de LLM) |
| `execution_steps` | Passos de cada execução (tool calls, llm_request, etc.) |
| `ai_agents` / `ai_variables` / `ai_tools` | Motor AGNO config-in-DB: agente, variáveis e tools com código Python no banco. O **prompt é inline em cada agente** (coluna `ai_agents.prompt`, texto livre próprio do agente — não reutilizável; `{placeholder}` resolvidos por `ai_variables`); editado no formulário do agente, não há mais aba/tabela de prompts compartilhados. `ai_tools` só é instalada/executada com `ai_tools_code_enabled=True` (kill-switch P62, default OFF) |
| `ai_prompts` / `ai_prompts_history` | **Legado** — eram templates de prompt reutilizáveis referenciados por `ai_agents.prompt_key`. Não são mais lidas na resolução do agente (o prompt agora é inline). Mantidas (não destrutivo) por compat; os endpoints `/api/ai/prompts*` continuam existindo mas não alimentam o motor |
| `ai_agents_history` / `ai_tools_history` | Snapshot por versão (save) de cada agente/tool. O snapshot do agente inclui o `prompt` inline, então Histórico/Reverter cobrem o prompt |
| `plugins` | Plugins descobertos no filesystem (id, version, enabled, load_error) |
| `plugin_migrations` | Versões de SQL migrations já aplicadas, por plugin |
| `plugin_<id>_*` | Tabelas criadas por plugins via suas migrations (prefixo obrigatório) |
| `tool_overrides` | Override por-tool (enabled, description, display_label). Row criada automaticamente para cada tool registrada (core + plugin) |

