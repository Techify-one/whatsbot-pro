# Plano 22 — Motor único (multi-agente), remoção total do legado

> **Objetivo:** o WhatsBot passa a ter **um só motor de IA**: o AGNO multi-agente
> (config-in-DB). Não existe mais "caminho legado" nem a flag `ai_engine_enabled`.
> O banner "Motor de IA (config-in-DB)" com os botões *Ativar/Desativar motor*
> some da tela. O comportamento da IA é controlado pelo interruptor global
> (`auto_reply`) + config por canal + IA por conversa — como já é hoje.

> **Como executar:** este plano é dividido em **fases**. Você pode rodar uma fase
> por vez com uma IA, colando a seção da fase como instrução. Onde várias fases
> podem ser feitas numa tacada só com segurança, há uma marcação
> **🟢 PODE AGRUPAR**. Onde é arriscado misturar, há **🔴 FAÇA SOZINHA**.

---

## Princípio de design

- `agent_factory.build_for_contact(...)` **sempre** devolve um `AgentSpec`. Nunca
  mais retorna `None` "para cair no legado".
- A resiliência por-requisição **continua**: uma falha isola **uma conversa**,
  nunca derruba o atendimento inteiro (o `try/except` é por mensagem/contato).
- O fallback deixa de ser "outro motor" e passa a ser: **cascata de resolução**
  (agente vinculado → agente default → prompt-semente) e, no caso raríssimo de
  nada resolver, **logar + gravar um card de erro no painel daquela conversa,
  sem enviar nada ao cliente**.

## Decisões já tomadas (não reabrir)

1. **Falha por conversa:** loga + card de erro (role `error`, painel-only) na
   conversa afetada. **O erro NUNCA chega ao cliente** (não envia mensagem).
2. **Prompt-semente:** vem de uma **constante no código** (`DEFAULT_SYSTEM_PROMPT`),
   usada só para semear o agente default no 1º boot. Depois, tudo vem do banco/tela.

## O que NÃO muda (não remover)

- `audio_model` / `image_model` / `document_model` em `config` e no handler —
  são da transcrição de mídia (chamadas diretas ao LLM, não-agênticas), usados
  **sempre**, independentes do motor.
- `_select_active_tools(spec)` com `tool_names=None` = todas as tools (é o
  comportamento normal do agente default — **não** é legado).
- Telas de Agentes / Prompts / Variáveis / Tools (config-in-DB) — fonte canônica.

---

## Mapa de arquivos afetados

| Área | Arquivo | Fase |
|---|---|---|
| Migração de dados | boot-step / migration | 1 |
| Factory do motor | `agent/agent_factory.py` | 2 |
| Handler | `agent/handler.py` | 3 |
| Wiring backend | `main.py`, `server/dev.py`, `server/routes/config.py`, `config/settings.py`, `agent/tools/transferir_agente.py` | 4 |
| Invariante default | rotas de `ai_agents` | 5 |
| Frontend | `web/static/js/components/ai/AgentEngine.js`, `web/static/js/components/SetupWizard.js` | 6 |
| Testes | `tests/test_agent_routing.py`, `tests/test_endpoints.py` | 7 |

---

## Fase 1 — Migração de dados (segurança primeiro) 🔴 FAÇA SOZINHA

> **Por quê primeiro:** preserva o prompt/modelo que o usuário já configurou
> antes de qualquer chave de `config` deixar de ser lida. Sem isso, quem usava o
> caminho legado com prompt customizado poderia perder o texto.

Criar um passo idempotente (no boot, junto de `seed_default_agent`, ou uma
migration Alembic dedicada) que, **antes** de qualquer remoção:

1. Se existir `config.system_prompt` **e** o prompt do agente `default`
   (`ai_prompts[default]`) estiver vazio ou igual à semente antiga →
   copia `config.system_prompt` → `ai_prompts[default].body`.
2. Se existir `config.model` **e** o `model_config.model` do agente `default`
   estiver vazio → copia `config.model` → `model_config.model` do agente default.
3. Não sobrescreve edições já feitas no banco (idempotente, sem version bump).

**Critério de aceite:** num banco que tinha `config.system_prompt` customizado, o
agente `default` passa a ter esse prompt no banco. Rodar duas vezes não muda nada.

---

## Fase 2 — `agent/agent_factory.py` 🟢 PODE AGRUPAR (com Fase 3)

1. Adicionar constantes no topo do módulo:
   - `DEFAULT_SYSTEM_PROMPT` = a string PT-BR de hoje (a mesma de
     `config/settings.py` DEFAULT_CONFIG).
   - `DEFAULT_MODEL = "deepseek/deepseek-v4-pro"`.
2. `seed_default_agent`: semear a partir das constantes
   (`DEFAULT_SYSTEM_PROMPT` / `DEFAULT_MODEL`), **não** de `settings.get(...)`.
3. Criar exceção tipada `class AgentResolutionError(Exception)`.
4. Reescrever `build_for_contact(handler, contact) -> AgentSpec`:
   - Remover o gate `if not getattr(handler, "ai_engine_enabled", False): return None`.
   - Cascata: agente vinculado → agente default → seed. Prompt vazio →
     `DEFAULT_SYSTEM_PROMPT`. Model vazio → `DEFAULT_MODEL`.
   - **Não** usar mais `handler.system_prompt` / `handler.model` (vão sumir).
   - Trocar o `except Exception: return None` por: logar e **`raise AgentResolutionError(...)`**
     apenas quando nada for resolvível (DB realmente quebrado). Sem retorno `None`.
5. Atualizar a docstring do módulo (remover menção a "legacy path"/"flag off").

**Critério de aceite:** com o banco normal, `build_for_contact` devolve o
`AgentSpec` do agente default quando não há vínculo; nunca devolve `None`.

---

## Fase 3 — `agent/handler.py` 🟢 PODE AGRUPAR (com Fase 2)

1. `__init__`: remover params/atributos `system_prompt`, `model`,
   `ai_engine_enabled`. **Manter** `audio_model`, `image_model`,
   `document_model`, `api_key` e os demais.
2. `update_config`: remover params `system_prompt`, `model`, `ai_engine_enabled`.
   **Manter** os media models.
3. `aprocess_message` **e** `process_message`:
   - `agent_spec` agora é sempre válido → eliminar os ternários
     `... if agent_spec else self.model` e `agent_spec.base_prompt if agent_spec else None`.
     Usar direto `agent_spec.model` / `agent_spec.base_prompt`.
   - Envolver a chamada `agent_factory.build_for_contact(...)` em
     `try/except AgentResolutionError`: ao falhar →
     `logger.error(...)` + `message_repo.add(conversation_id=..., role="error", content=...)`
     + `broadcast("new_message", ...)` + **return sem enviar nada ao cliente**.
4. `_build_system_prompt`: `base_prompt` sempre presente → remover o
   `else self.system_prompt`.
5. **Não** mexer em `_select_active_tools` (o ramo `tool_names is None` = todas as
   tools continua válido para o agente default).

**Critério de aceite:** processar uma mensagem usa sempre o agente do banco;
forçar um erro de resolução grava card `error` na conversa e **não** envia
mensagem ao cliente; o resto do atendimento segue.

---

## Fase 4 — Wiring backend (remoção das chaves legadas) 🟢 PODE AGRUPAR

> Fazer só **depois** das Fases 1–3 (a migração já preservou os dados e o handler
> já não depende das chaves).

1. `main.py` e `server/dev.py`: remover `system_prompt`, `model`,
   `ai_engine_enabled` da construção do `AgentHandler`. **Manter** media models +
   `api_key`.
2. `server/routes/config.py`:
   - Remover `system_prompt`, `model`, `ai_engine_enabled` de `allowed_keys`.
   - Remover essas chaves do retorno do `GET /api/config`.
   - Remover a função `_mirror_globals_to_default_agent` e suas chamadas.
   - Tirar esses params da chamada `agent_handler.update_config(...)`.
   - **Manter** `audio_model`/`image_model`/`document_model`.
3. `config/settings.py`:
   - Remover do `DEFAULT_CONFIG`: `system_prompt`, `model`, `ai_engine_enabled`.
   - Remover o env override `WHATSBOT_AI_ENGINE` (e o mapeamento em `_ENV_OVERRIDES`).
   - **Manter** `audio_model`/`image_model`/`document_model`.
4. `agent/tools/transferir_agente.py`: remover o gate
   `if not getattr(ctx.handler, "ai_engine_enabled", False): return "Erro..."`
   (o roteamento entre agentes agora está sempre disponível).

**Critério de aceite:** `grep -rn "ai_engine_enabled" .` (fora de docs-planos)
não retorna mais nada no código de runtime; o app sobe e responde normalmente.

---

## Fase 5 — Invariante do agente default 🔴 FAÇA SOZINHA

> Garante que a cascata da Fase 2 sempre tem onde aterrissar.

Nas rotas de `ai_agents` (procurar o arquivo de rotas dos agentes, provavelmente
`server/routes/ai*.py` ou similar — **confirmar na implementação**):

1. Bloquear **desabilitar** o agente `DEFAULT_AGENT_KEY` → 400 com mensagem clara
   ("o agente padrão não pode ser desativado").
2. Bloquear **excluir** o agente `DEFAULT_AGENT_KEY` → 400 com mensagem clara.

**Critério de aceite:** tentar excluir/desabilitar o agente padrão pela API
retorna 400; agentes não-default continuam podendo ser excluídos/desabilitados.

---

## Fase 6 — Frontend 🟢 PODE AGRUPAR

1. `web/static/js/components/ai/AgentEngine.js`:
   - Remover **todo** o banner do "Motor de IA" (linhas ~119–148): título, badge
     Ativo/Desligado, e o botão "Ativar/Desativar motor".
   - Remover o estado `engineOn` / `engineBusy`, a função `toggleEngine` e o
     `useEffect` que chama `getConfig`.
   - Remover imports não usados (`getConfig`, `saveConfig`).
   - **Manter** o botão **"Reiniciar worker"** (`handleRestart` / `restartAi`) —
     ainda é necessário para tools code-in-DB. Colocá-lo num cabeçalho enxuto.
2. `web/static/js/components/SetupWizard.js`:
   - Passo 3 (prompt do agente): salvar o prompt **direto no agente default no
     banco** (endpoint de `ai_prompts` / prompt default — **confirmar o endpoint
     na implementação**), em vez de `onConfigSave({ system_prompt })`.
3. `web/static/js/components/ai/GeneralSettings.js`: conferir que não sobrou
   nenhuma referência a `ai_engine_enabled` (não deve ter — só o interruptor
   global `auto_reply`, a chave de API e o aviso de saldo).

**Critério de aceite:** a tela "Engine de IA" não mostra mais o botão de
ligar/desligar motor; o wizard novo grava o prompt no agente default; recarregar
não mostra erros no console.

---

## Fase 7 — Testes 🟢 PODE AGRUPAR (rodar por último)

1. `tests/test_agent_routing.py`: remover `ai_engine_enabled` dos mocks do
   handler (motor sempre ligado).
2. `tests/test_endpoints.py`:
   - Ajustar asserts de config que esperem `system_prompt`/`model`/`ai_engine_enabled`.
   - Adicionar caso: agente `default` não pode ser desabilitado/excluído (Fase 5).
3. Rodar:
   ```bash
   source venv/Scripts/activate   # ou ./venv/bin/activate no Linux
   python tests/test_endpoints.py
   # + o teste de routing, se separado
   ```

**Critério de aceite:** suíte verde.

---

## Ordem recomendada de execução

1. **Fase 1** sozinha (migração de segurança) — validar num banco com prompt customizado.
2. **Fases 2 + 3 juntas** (factory + handler) — o coração da mudança; testar processamento de mensagem.
3. **Fase 4** (remoção das chaves) — `grep` por `ai_engine_enabled` deve zerar.
4. **Fase 5** sozinha (invariante do default).
5. **Fase 6** (frontend).
6. **Fase 7** (testes) — fechamento.

## Riscos e mitigação

- **Perda de prompt customizado** → resolvido pela Fase 1 (migração antes da remoção).
- **IA muda numa conversa** → não acontece: cascata + card de erro isolado;
  cliente nunca recebe erro.
- **Chaves órfãs em `config`** de instalações antigas → inertes (ninguém lê);
  opcional limpar via migration depois.

## Pontos a confirmar durante a implementação (não bloqueiam o plano)

- Endpoint exato para o wizard salvar o prompt do agente default (Fase 6.2).
- Arquivo/rotas onde travar disable/delete do `DEFAULT_AGENT_KEY` (Fase 5).
