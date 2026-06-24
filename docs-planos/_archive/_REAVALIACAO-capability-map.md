# Capability map — o que JÁ EXISTE no código depois do update (origin/main + branch)

> Fonte da verdade para a reavaliação dos planos. **ATUALIZADO após `git pull` em 2026-06-19:
> local == origin/main == `58586e1`.** Inclui dois commits posteriores ao levantamento inicial:
> `953bca9`/`71ed713` (PR #8 — fix split_messages JSON) e `58586e1` (REMOVE o multi-agente Team).
> Ver seções 4 e 4b. O restante do mapa foi levantado por análise direta dos diffs de origin/main.

## Commits novos em `origin/main`
- `fe39af2` feat(agent): **motor AGNO com multi-agentes (Team) e AI engine dirigido pelo banco** ← o grande
- `ccaebc4` chore(plugins): mantém só `lembretes` como exemplo bundled; resto foi pra "Loja"
- `d8dd3d4` feat(plugins): barra de progresso ao exportar plugin
- `a0008be` feat(plugins): **instala dependências pip declaradas no manifest ao carregar**

## Document transcription — JÁ MERGEADA em `origin/main` via PR #7 (`f29eeb5`, 2026-06-19)
- `ca8e3d9` feat: reconhecimento/transcrição de documentos (PDF via LLM, DOCX/text via stdlib)
- `143021f` fix(wizard): trata reach-out timelock (erro 463) + GOWA 8.8.0
- Topo atual de `origin/main` = `f29eeb5` (merge). Local `c2b5a03` está 5 commits atrás (precisa `git pull`).
- Merge limpo, sem edições de resolução de conflito. Impacto nos planos: nenhum (feature independente).

---

## 1. Motor AGNO (substitui o handler legado — NÃO é flag)
- `agent/agno_engine.py` (+429): usa o **framework AGNO real** (`from agno.agent import Agent`,
  `agno.team.Team/TeamMode`, `agno.models.openai.OpenAILike`). `agno` no requirements.txt (SEM pin).
- **O loop manual de tool-calling do OpenAI foi REMOVIDO de `handler.py`.** Toda mensagem agora roda
  por `agno_engine.run_async/run_sync`. NÃO há flag para voltar ao loop antigo — o legado de
  tool-calling sumiu (só sobra o fallback de prompt/config quando `ai_engine_enabled=off`).
- Engine é stateless por request: cria `Agent`/`Team` por mensagem; closures de tool capturam um
  coletor `executed` por request (sem cross-talk entre contatos concorrentes).
- WhatsBot continua dono de: system prompt, histórico (montado das `messages`), lista de tools,
  filters/events do bus, usage e save. AGNO só faz o núcleo de raciocínio. Os contextos do AGNO
  (history, telemetry, retries, build_context) são todos DESLIGADOS.
- Cada tool vira um `agno.tools.function.Function` cujo entrypoint **re-aplica o pipeline de plugin
  completo**: `filter.tool.args` → `tool.before` → `handler._dispatch_tool` → `tool.after` →
  `filter.tool.result`. Semântica idêntica ao dispatch antigo.
- Usage vem de `RunMetrics` (não de `response.usage`); handler ganhou `_record_usage_tokens()`.

## 2. AI Engine config-in-DB (flag `ai_engine_enabled`, default OFF)
- Tabelas novas em `db/tables.py` (migration `0007_ai_engine_tables`):
  - `ai_agents` (PK `agent_key`, `display_name`, `prompt_key`, `model_config` JSON, `tool_names` JSON
    array ou NULL=todas, `enabled`, `version`, `updated_at`)
  - `ai_prompts` (PK `prompt_key`, `body` template com `{placeholders}`, `version`, `updated_at`)
  - `ai_variables` (PK `name`, `value`, `category`, `updated_at`) — substituição `{name}` no prompt
  - `ai_tools` (PK `name`, `description`, `code` Python, `dependencies` JSON pip, `enabled`,
    `install_status` pending/installing/ok/failed, `install_error`, `installed_deps`, `version`)
  - `ai_agents_history`, `ai_prompts_history`, `ai_tools_history` (snapshot por save; SEM history de variables)
  - `executions` ganhou `agent_key`, `total_tokens`, `total_cost_usd` (colunas criadas, mas o writer
    AINDA NÃO as popula — schema à frente do código)
- `agent/agent_factory.py` (+121): com a flag ON, resolve **prompt + model_config + seleção de tools**
  do banco POR REQUEST (sem restart). `render_template` faz substituição `{var}` só de vars conhecidas.
  `seed_default_agent` semeia `default` prompt+agent no boot (idempotente). Fallback gracioso ao
  legado se flag OFF, agente default ausente/disabled, ou QUALQUER exceção.
- **CÓDIGO de tool exige restart** (installer re-roda no boot).
- Rotas `server/routes/ai_engine.py` sob `/api/ai`: CRUD de agents/prompts/variables/tools (PUT por
  key, sem POST create). PUT/DELETE de tools agenda restart. Emite evento `ai.config.changed`.
  **NÃO há endpoint de history/rollback** (tabelas populadas mas sem API de leitura).
- `GET/PUT /api/config` agora aceitam `ai_engine_enabled`.

## 3. Code-in-DB tools (installer) — ⚠️ RODA IN-PROCESS
- `agent/ai_tool_installer.py` (+209): para cada `ai_tools` enabled → instala deps (`pkg_deps`) →
  **escreve `row["code"]` em `storages/ai_tools/<name>.py` e importa via `exec_module`** sob
  `whatsbot_ai_tools.<name>` → registra no MESMO registry das tools core/plugin.
- ⚠️ **Execução é IN-PROCESS, no boot, com privilégios totais do processo** (DB, FS, rede, segredos).
  NÃO há subprocess isolado / RLIMIT / timeout / sem-segredos — diverge da decisão P62 do plano 06.
- Fail-closed: erro → `install_status=failed`, tool não registra, app sobe normal.
- Colisão de nome: registry faz no-op (código/plugin têm precedência sobre banco) — bate com P61.
- Validação de nome `^[a-z][a-z0-9_]{0,63}$`. Roda em `create_app` DEPOIS de core+plugin tools.

## 4. Multi-agente Team — ❌ REMOVIDO (commit 58586e1, após o pull de 2026-06-19)
- O suporte a Team/TeamMode foi REMOVIDO do `agno_engine.py`; as flags `multi_agent_enabled`/
  `agent_team_mode`/`agents` saíram do settings/handler/call sites. **O engine agora roda SEMPRE um
  Agent único.** A infra `ai_agents`/`ai_engine_enabled` (config-in-DB single-agent) permanece intacta.
- Efeito na reavaliação: o problema dos "dois caminhos de config desconexos" (Team config-driven vs
  ai_agents) DESAPARECEU — sobra UM caminho (ai_agents single-agent, flag OFF por default). Multi-agente
  passou a ser simplesmente NÃO IMPLEMENTADO (adiado), sem dívida de config divergente. P60 deixa de
  ser "contradito" e volta a ser "intacto/futuro" (depende de inboxes do plano 01).
- ⚠️ AINDA ASSIM: `server/dev.py` continua NÃO passando `ai_engine_enabled` ao handler (o commit
  58586e1 tocou dev.py só pra remover as flags multi-agente). No launcher de dev/hot-reload o
  config-in-DB nunca liga. Bug ainda válido.

## 4b. Split de mensagens — endurecido (PR #8, 71ed713)
- `_encode_history_for_split`: o histórico do assistant agora é reenviado ao LLM no MESMO formato
  JSON (array de strings) que ele deve produzir, em vez de texto puro já dividido. Com tools ativas,
  a aderência ao formato subiu de ~1/10 para 15/15 (deepseek-v4-pro). Só muda a cópia enviada ao LLM;
  histórico salvo e painel intactos. Reforça o bloco de formato no system prompt com exemplo.
- ⚠️ Continua NÃO sendo `output_schema` Pydantic (P64) — é um fix de encoding de histórico + prompt,
  não structured output do AGNO. P64 segue NÃO FEITO, mas o caminho legado de split ficou bem mais robusto.

## 5. pip-deps compartilhado (plugins + AI tools)
- `plugins/pkg_deps.py`: choke-point único de `pip install` usado por plugins E por AI tools.
  `is_dep_allowed()` é **allowlist ABERTA (retorna True pra tudo)** no MVP — bate com P66 (sem
  allowlist). Recusa em build frozen/PyInstaller. Timeout 600s. `importlib.invalidate_caches()`.
- Plugins: `plugin.yaml` pode declarar `dependencies: [pip-specs]`; `plugins/loader._ensure_plugin_deps`
  roda ANTES de importar o módulo. Cache marker `plugins.installed_deps` (migration `0008`). Falha →
  `load_error`, plugin pulado, app sobe.

## 6. Loja de plugins + bundled
- "Loja de Plugins" = **apenas um link externo** (`https://whatsbot.techify.one/plugins`) em
  `PluginsManager.js`. NÃO é store in-app, sem fetch/catálogo/install. Instalação continua por
  upload de `.zip` (`POST /api/plugins/import`).
- Bundled em `assets/plugin_examples/` agora: **só `lembretes`**. Removidos auto_signature, blacklist,
  custom_sounds, event_logger, horario_funcionamento, notifications, transcricao_grupos (foram pra Loja).
  ⚠️ CLAUDE.md está DESATUALIZADO (ainda lista os 8 + "11 tabelas").

## 7. Document transcription (branch, não mergeada)
- `handler.transcribe_document()`: PDF → LLM (`document_model`, default `google/gemini-2.5-flash`,
  via content part `file` base64); DOCX → stdlib (unzip word/document.xml); text → read_text. Cap 20k
  chars. Config `document_transcription_enabled` (default True). Integra com `filter.transcription.*`.
  Wired no webhook (3 sites) + sandbox. requirements: +`segno` (QR do wizard, não doc lib).
- Timelock erro 463 (anti-spam de iniciar chats): `gowa/client` tipa `reachout_timelock`; wizard cai
  num fluxo manual (deep link wa.me + QR via segno). GOWA 8.5.0 → 8.8.0.

---

## Impacto resumido nos planos (a aprofundar por plano)
- **Plano 06 (motor multi-agente / code-in-DB):** Fases 0-2 essencialmente FEITAS; Fase 3 feita mas
  IN-PROCESS (diverge de P62); Fase 4-6 parciais (Team config-driven, não DB/inbox; sem structured
  output P64; versioning/history feitos mas sem API). Reabre P62 (isolamento), P60 (agente↔inbox),
  P64 (output_schema), P67 (subprocess do tool_runner), P65 (coexistência — já é Agno-only).
- **Plano 09 (fundação runtime):** o tool_runner do 06 já existe IN-PROCESS sem o serviço de
  subprocesso → muda a premissa de P67. `pkg_deps` é infra nova compartilhada não prevista.
- **Plano 02 (canais):** plugin pip-deps ajuda a extração do GOWA-plugin (deps declaradas).
- **Plano 00 (mestre):** Onda 5 foi parcialmente puxada pra frente, fora de ordem das dependências.
- **Planos 01/03/04/05/08:** independentes do motor de IA — checar só ripples (ex: agente↔inbox no 01).
