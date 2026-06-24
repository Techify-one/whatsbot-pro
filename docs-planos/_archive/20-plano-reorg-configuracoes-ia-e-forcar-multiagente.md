# Plano de Implementação — 20: Reorganizar configurações da IA + **forçar multi-agente** (aposentar o single-agent legado)

> A tela do **motor de IA** (`web/static/js/components/ai/`) tem duas abas: **Configurações**
> (`GeneralSettings.js`, global) e **Agentes** (`AgentsManager.js`, por agente — cada agente já tem **prompt**
> e **modelo** próprios). Hoje as configs estão mal distribuídas entre essa tela e o **painel de Configurações**
> (engrenagem → `ConfigPanel.js`).
>
> **Decisões do produto (travadas):**
> 1. **Tirar prompt e modelo GLOBAIS** da tela de Configurações da IA — cada agente já os tem.
> 2. **Forçar o multi-agente:** aposentar o caminho single-agent legado. Como "sempre começa com um agente"
>    (o agente default), o efeito é o mesmo. `ai_engine_enabled` passa a ser **ligado por padrão**; o agente
>    default vira a fonte canônica de prompt/modelo.
> 3. O **agente default já vem com as tools**: **Salvar Dados do Contato** (`save_contact_info`), **Transferir
>    para Humano** (`transfer_to_human`), **Preencher Atributo Personalizado** (`set_custom_attribute`),
>    **Transferir para outro agente** (`transferir_agente`).
> 4. **Mover do painel → tela da IA:** Chave de API, descrever imagem, ler documento, transcrição de áudio,
>    automação (ligar IA p/ responder mensagens e novos contatos), resposta em grupos.
> 5. **Permanecem no painel:** marcar conversas, avisos de sistema no chat, senha do painel, banco de dados.
>
> **Restrição técnica crítica (verificada):** os valores globais `model` e `system_prompt` são **fallback
> obrigatório** do código. Dá para **tirar os controles** da UI, mas **não dá para apagar os valores** — sob
> pena de o modelo resolver para `None` e a chamada ao provedor falhar. Ver §0/§2.

---

## 0. Estado atual VERIFICADO (2026-06-22, branch `developer`)

### Onde cada coisa está (inventário)
- **`ConfigPanel.js`** (engrenagem), seções:
  - **Automação** (`:188`): `auto_reply` (master: IA responde mensagens), `default_ai_enabled` (IA ligada p/
    novos contatos), `group_reply_mode` (`:209`).
  - **API e Modelos** (`:225`): `openrouter_api_key` (+ `handleTestKey` `:75-101`, auto-save no válido),
    `image_transcription_enabled` (`:255`), `document_transcription_enabled` (`:281`),
    `audio_transcription_mode/target/chat_prefix` (`:312-350`).
  - **Marcar conversas** (`:355`), **Avisos de sistema no chat** (`:415`, 5 toggles `system_notice_*`),
    **Avançado** (`:482`: `max_executions`, `audit_retention_days`, `web_password`), `<DatabaseSettings/>` (`:559`).
- **`ai/GeneralSettings.js`** (tela da IA → Configurações): `model` (`:102-110`), `system_prompt`,
  `max_context_messages`, `message_batch_delay`, `split_messages`/`split_message_delay`,
  `transfer_alert_enabled`/duração, `low_balance_enabled`/`low_balance_threshold`. Salva via `PUT /api/config`.
- **`ai/AgentsManager.js`**: cada agente tem `prompt_key` (corpo em `ai_prompts.body`) e
  `model_config.model`; o seletor de modelo expõe `— padrão do app —` (vazio) e o texto diz que um agente sem
  prompt "usa o system prompt padrão do app" (`:199`, `:204-211`). **Confirma que prompt/modelo globais são
  fallback, não config primária.**

### Como prompt/modelo globais são consumidos (o motivo de não poder apagar)
- `ai_engine_enabled` default **False** ([`settings.py:94`](../config/settings.py#L94)); `auto_reply` default
  **False** (`:69`); `model` default `"deepseek/deepseek-v4-pro"` (`:61`, **não-vazio**); `system_prompt` tem
  default (`:65`).
- [`agent_factory.build_for_contact`](../agent/agent_factory.py#L125): se `ai_engine_enabled` off → `return None`
  (`:132`) e o handler usa `self.system_prompt` + `self.model` (= `config['system_prompt']`/`config['model']`,
  caminho **legado single-agent**).
- Com o motor ON, um agente sem prompt cai em `handler.system_prompt` (`:145`) e **sem modelo** cai em
  `handler.model` (`:152`). → **Apagar o valor de `model` deixaria `id=None` → erro do provedor.**
- `seed_default_agent(settings)` (`:62-82`): cria `DEFAULT_PROMPT_KEY` (corpo = `config['system_prompt']`) e
  `DEFAULT_AGENT_KEY` (`model_config.model = config['model']`, `tool_names=None` = **todas** as tools core,
  `enabled=True`). `CORE_TOOLS` (`agent/tools/__init__.py:35`) = exatamente as 4 tools pedidas.
- Transcrição/descrição usam `audio_model`/`image_model`/`document_model` (**separados**, não `config['model']`);
  `test_api_key` usa modelo hard-coded. Removidos do escopo de prompt/modelo de chat.
- `SetupWizard` passo 3 (`:186-191`) escreve `config['system_prompt']` direto — **precisa ser repontado**.
- `PUT /api/config` (`config.py` `allowed_keys` `:80-97`; `update_config` `:124-135`) recebe `system_prompt` e
  `model`; o handler é construído com eles no boot (`server/dev.py:56-69`).

---

## 1. Decisões de design (travadas)

1. **Multi-agente sempre on.** `ai_engine_enabled` default **True** (e seed do agente default no boot). O
   caminho legado **não é removido do código** — `build_for_contact` ainda devolve `None` em row quebrada e o
   handler cai no fallback in-code. Isso é uma **rede de segurança**, não uma opção de UI.
2. **Valores de fallback preservados.** `config['model']` e `config['system_prompt']` continuam em
   `DEFAULT_CONFIG` (não-vazios) e em `allowed_keys`. **Some o controle de UI**, não o valor.
3. **Fonte canônica = agente default.** O modelo/prompt "global" passa a ser editado **em `AgentsManager`**
   (o agente default). Para sincronizar o fallback legado, ao salvar o modelo/prompt do agente default,
   **espelhar** em `config['model']`/`config['system_prompt']` (mantém a rede de segurança coerente).
4. **Modelo do agente default obrigatório** (não-vazio) — garante que o resolve nunca dê `None`. Outros agentes
   podem deixar `— padrão do app —` (cai em `config['model']`, que tem default não-vazio).
5. **Tools default** = as 4 core (já é o efeito de `tool_names=None`). Documentar explicitamente no seed.
6. **Movimentações de UI** conforme §3 (sem mudar config keys nem backend de cada setting — só onde o controle
   é renderizado; tudo persiste pelo mesmo `PUT /api/config`).
7. **Wizard** passo 3 grava o corpo do `DEFAULT_PROMPT_KEY` (prompt do agente default) em vez de
   `config['system_prompt']` (ou ambos, para manter o fallback). Garantir que o agente default tenha modelo
   (seed a partir de `DEFAULT_CONFIG['model']`).

---

## 2. Backend — forçar multi-agente com segurança
- **Flag:** `DEFAULT_CONFIG["ai_engine_enabled"] = True`. Migration leve: instalações existentes com a chave
  ausente/False — decidir entre (a) respeitar o valor salvo (só novas instalações ligam) ou (b) forçar True no
  upgrade. Recomendado **(b)** com seed garantido, já que o objetivo é aposentar o legado. **Pré-condição:**
  `seed_default_agent` roda no boot **antes** de atender mensagens (hoje roda ao ligar o motor — mover para o
  bootstrap).
- **Seed garantido + não-vazio:** `seed_default_agent` exige `config['model']` não-vazio (já tem default). Se,
  por env, vier vazio, usar o default de `DEFAULT_CONFIG`. `tool_names=None` (4 tools core) — manter.
- **Espelho default-agent → config:** ao `PUT` do agente default em `AgentsManager` (modelo/prompt), também
  gravar `config['model']`/`config['system_prompt']` (fonte única de verdade p/ o fallback). Evita drift.
- **Não tocar** em `audio_model`/`image_model`/`document_model` nem em `test_api_key`.

## 3. Frontend — mover os controles
- **`ConfigPanel.js` → remover** as seções **Automação** e **API e Modelos** inteiras (mover JSX + state +
  handlers: `apiKey`/`handleTestKey`, `image/document_transcription_enabled`, `audio_transcription_*`,
  `auto_reply`, `default_ai_enabled`, `group_reply_mode`). **Manter** Marcar conversas, Avisos de sistema,
  Avançado (retention + senha), DatabaseSettings.
- **`ai/GeneralSettings.js`:**
  - **Remover** os controles de **`model`** e **`system_prompt`** (mantêm-se as chaves no save? Não enviá-las
    mais por aqui — elas passam a ser geridas pelo agente default; ver §2 espelho). Manter `max_context_messages`,
    `message_batch_delay`, `split_messages`, low balance.
  - **Adicionar** os blocos movidos: Chave de API (+ test/auto-save), descrever imagem, ler documento, bloco de
    transcrição de áudio, automação (`auto_reply` + `default_ai_enabled`), resposta em grupos
    (`group_reply_mode`). Reaproveitar os componentes/handlers que vieram do `ConfigPanel`.
- **`AgentsManager.js`:** modelo do **agente default** obrigatório (sem `— padrão do app —` para ele);
  manter `— padrão do app —` para os demais. Deixar claro que o agente default é o "global".
- **`SetupWizard.js`:** passo 3 grava o prompt do agente default (e opcionalmente espelha `config['system_prompt']`).
- **Dark mode:** todos os blocos movidos usam classes `wa-*`/`.wa-field` (já usam no `ConfigPanel`).

## 4. Testes
- **Boot multi-agente:** com `ai_engine_enabled=True`, agente default existe com modelo não-vazio + 4 tools core;
  responder mensagem usa o agente default (não o legado).
- **Fallback intacto:** agente sem modelo (`— padrão do app —`) resolve para `config['model']` (não-`None`).
- **Espelho:** `PUT` modelo do agente default reflete em `config['model']`.
- **Config keys preservadas:** `PUT /api/config` ainda aceita as chaves movidas; nenhuma some do `allowed_keys`.
- **Painel:** `ConfigPanel` não renderiza mais Automação/API; `GeneralSettings` renderiza os blocos movidos.
- **Wizard:** passo 3 popula o prompt do agente default; fresh install responde sem prompt vazio nem modelo nulo.

## 5. Checklist
- [ ] `ai_engine_enabled` default True + seed do agente default no bootstrap (antes de atender).
- [ ] Espelho default-agent (modelo/prompt) → `config['model']`/`config['system_prompt']`.
- [ ] Modelo do agente default obrigatório; manter fallback não-vazio em `DEFAULT_CONFIG`.
- [ ] Documentar `tool_names=None` = 4 tools core no seed.
- [ ] `ConfigPanel`: remover Automação + API e Modelos (state/handlers/JSX).
- [ ] `GeneralSettings`: remover prompt/modelo globais; adicionar API key + transcrições + automação + grupos;
      manter contexto/batch/split/low-balance.
- [ ] `AgentsManager`: modelo obrigatório no agente default.
- [ ] `SetupWizard` passo 3 repontado.
- [ ] Testes 4.x; modo escuro nos blocos movidos.

## 6. Riscos
- **Apagar o VALOR de `model`/`system_prompt`** quebra tudo (modelo `None`). O plano só remove os **controles**;
  os valores ficam como fallback. **Nunca** retirar de `DEFAULT_CONFIG`/`allowed_keys`.
- **Instalações com `ai_engine_enabled=False`** hoje: se forçar True sem seed garantido, primeira mensagem pode
  cair no fallback legado com prompt do wizard — aceitável, mas o seed no boot evita surpresa.
- **Drift modelo global vs agente default** se o espelho (§2) não for feito — definir o agente default como fonte
  única.
- **Wizard órfão** se o passo 3 não for repontado (fresh install sem prompt). Tratado em §3.
- **Cruza com plano 17:** `auto_reply`/`default_ai_enabled` mudam de lugar de UI mas seguem governando o gate
  (P17) e o `ai_active` inicial das conversas novas.
