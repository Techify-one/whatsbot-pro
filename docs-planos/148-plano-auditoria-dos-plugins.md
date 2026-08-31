# Plano 148 — Auditoria dos plugins: o que os plugins fazem e não registram

> **Status:** 🟢 EXECUTADO (2026-08-31) — as 32 lacunas fechadas, release publicado localmente (ver §9) · **Escopo:** médio (11 plugins; 30 call sites; sem migration; sem mudança de core, com **duas exceções declaradas** em §0)
> **Origem:** mesma investigação do **[plano 147](147-plano-auditoria-do-core-o-que-nao-e-registrado.md)** — 41 agentes de leitura com verificação adversarial por finding. Das 125 lacunas confirmadas, **32 são de plugin** e estão aqui.
> **Dependência:** executar **depois** do plano 147. Dois itens deste plano (§4.9 e §4.10) só ficam de pé com seams que o 147 entrega.
> **O quê/porquê:** o seam `plugins.context.audit(...)` existe, funciona e **é bem usado por alguns plugins** — em produção há 31 ações de plugin distintas contra 17 do core. O problema é a **assimetria**: o mesmo plugin audita a rota de configuração e ignora a rota que fecha o atendimento; um plugin de canal grava no canal e o irmão grava no plugin. Não é falta de mecanismo, é falta de cobertura consistente.
>
> **Como usar este plano:** ao executar cada plugin, preencha o "Status" dele ANTES de passar ao próximo — cada um é um `.zip` independente.

---

## 0 — Decisões a travar antes de executar

| # | Decisão | Consequência |
|---|---------|--------------|
| D1 ⬜ | O padrão é o helper `_audit` defensivo de [docs/PLUGINS_AUDITAVEIS.md §3](../docs/PLUGINS_AUDITAVEIS.md) — import `try/except`, nunca levanta, sempre **depois** do sucesso, com snapshot do `before` **antes** da escrita. | Nenhum plugin inventa mecanismo próprio. |
| D2 ⬜ | Plugin de **canal** grava `resource_type="channel"` + `resource_id=<channel_id>`; o resto grava o default `plugin:<id>`. | Corrige a assimetria hoje existente entre os seis canais. |
| D3 ⬜ | **`vendas_ia` tem uma correção que não pode esperar** (senha do Postgres em claro na trilha, em produção **agora**). Ela sai do plano e vira correção imediata, ou o plano inteiro vira urgente? | Ver §4.1. A metade que fecha o buraco de verdade é do core (plano 147, item 57). |
| D4 ⬜ | Duas correções **precisam de core** e por isso ficam condicionadas ao 147: o ator real em rota que o plugin autentica (`instagram`) e a máscara derivada do schema de Settings. | Sem o 147, `instagram` fica na alternativa barata (§4.9). |
| D5 ⬜ | `protocolos` concentra **12 das 32 lacunas**. Ele vai numa tranche própria ou junto? | O plugin é o maior do parque e o `.zip` dele é o mais arriscado de publicar. |

---

## 1 — Onde o código vive (ler antes de tocar em qualquer arquivo)

🚫 **Editar `storages/plugins/<id>/` não é fazer a correção** — aquilo é a cópia *instalada*, gitignorada, e some no próximo redeploy sem persistência. Nada sincroniza os quatro lugares automaticamente.

| Plugin | Fonte a editar | Como chega em produção |
|---|---|---|
| **10 dos 11** deste plano | `../whatsbot-pro-plugins/plugins/<id>/src/` (todos os 11 têm `src/`, confirmado) | `python3 scripts/build_plugins.py <id>` → `.zip` → **Importar (.zip)** na UI |
| **`gowa`** (bundled) | ⚠️ `assets/plugin_examples/gowa/` **neste repositório** | o boot copia para `storages/plugins/gowa/` com upgrade version-aware — corrigir só em `storages/` **é perdido no próximo bump** |

**Antes de buildar qualquer coisa** (armadilhas já vividas, registradas na memória do projeto):

1. `git fetch` no repositório de plugins **e** conferir a tabela `plugins` de **produção** — versões podem ter sido publicadas direto lá, fora do seu clone.
2. `build_plugins.py --check` mente "outdated" quando o zip nasceu com `umask` 664 em vez de 644 — **não rebuilde para "consertar"**, é o caminho destrutivo.
3. Uma pasta de plugin fora do catálogo aborta o build de **qualquer outro** — limpar WIP não rastreado antes.
4. `--check` compara zip × src: ele **não vê** arquivo que sumiu da fonte (foi assim que a 1.26.0 do `protocolos` regrediu).
5. **Instalar no local e testar antes de commitar/publicar.**

---

## 2 — O padrão de call site (copiar isto)

```python
PLUGIN_ID = "meu_plugin"

try:                                   # core anterior ao seam: degrada, não quebra
    from plugins.context import audit as _core_audit
except ImportError:                    # pragma: no cover
    _core_audit = None

def _audit(action: str, **kw) -> None:
    if _core_audit is None:
        return
    try:
        _core_audit(PLUGIN_ID, action, **kw)
    except Exception:                  # noqa: BLE001 — auditoria nunca derruba a ação
        pass
```

E na rota:

```python
antes = logic.get_config()            # 1. snapshot ANTES da escrita
data  = logic.set_config(body or {})  # 2. a ação real
_audit("config.update", before=antes, after=data)   # 3. DEPOIS do sucesso
return _ok(data)
```

⚠️ **Formato da ação**: `^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,3}$` — id do plugin + 1 a 3 segmentos. Fora do formato a linha é **descartada com WARNING**, sem quebrar a rota. Todas as ações propostas neste plano foram conferidas contra a regex.

⚠️ **Segredo nunca entra**: registre `{"chave_definida": True}`, nunca o valor.

---

## 3 — Panorama: quem audita o quê hoje

| Plugin | Chamadas `audit()` hoje | Lacunas confirmadas |
|---|---|---|
| `protocolos` | 6 | **12** |
| `melhorias` | 15 | **6** |
| `retornos` | 11 | **3** |
| `vendas_ia` | 3 | **2** (1 é vazamento ativo) |
| `debug_bus` | 0 | **2** |
| `gowa` | 3 | **2** |
| `janela_72h` | 0 | **1** (alta) |
| `utm_atendente` | 0 | **1** |
| `agendamento_retorno` | 0 | **1** |
| `trackify` | 9 | **1** |
| `instagram` | 6 | **1** (depende do 147) |
| `pagamentos` · `fechamento_ia` · `rotinas_ia` · `telegram` · `whatsapp_cloud` · `website` · `facebook_messenger` · `guarda_ia` · `etapa_comercial` | 12 · 4 · 4 · 5 · 8 · 3 · 5 · 0 · 0 | **0 confirmadas** |

Os últimos nove passaram na varredura — ou já cobrem o ciclo, ou não têm rota mutante administrativa. (Nove candidatas nesses plugins foram levantadas e **refutadas** na verificação adversarial: o gesto alegado não existia na tela, ou já era auditado por outro caminho.)

---

## 4 — As lacunas, plugin a plugin

### 4.1 🔴 `vendas_ia` — senha do banco em claro na trilha (**urgente**)

| Sev. | O que acontece |
|---|---|
| **alta** | Salvar a configuração do Vendas IA grava, sim, uma linha — **com a senha do Postgres do Nexus em claro dentro dela** ([settings.py:23](../storages/plugins/vendas_ia/settings.py#L23)), legível por qualquer um com `audit.read` e exportável em CSV. |

**Confirmado em produção** (consulta somente-leitura, 2026-08-28): **5 linhas** de `plugin.settings_update` com `resource_id='vendas_ia'` (ids 14, 248, 308, 522, 548) carregam o DSN completo com a senha em `after_json`, e 4 delas também em `before_json`. A mais antiga é de **14/07/2026** — está lá há mais de seis semanas.

⚠️ **A prova do mecanismo está na mesma linha**: nela, `openrouter_api_key` aparece como `***` e `nexus_dsn` aparece inteiro. O mascaramento **funcionou** — ele só casa **nome exato** de chave, e `nexus_dsn` não está na lista. Não é um mascaramento quebrado; é um mascaramento que, por construção, só protege o que alguém lembrou de listar.

**Duas frentes independentes:**

- **(a) no core** (plano 147, item 57): acrescentar `nexus_dsn`, `dsn`, `database_url`, `connection_string` à denylist — ou casar por padrão `/(^|_)(token|secret|password|senha|api_?key|dsn)(_|$)/i`. ⚠️ **Não use substring solta**: mascararia os booleanos de diagnóstico (`token_set`, `app_secret_set`, `*_hint`).
- **(b) no plugin** — a que fecha o buraco de verdade: **tirar `nexus_dsn` da settings declarativa** e movê-lo para rota própria com sentinela write-only, exatamente como `trackify` já faz. Enquanto ele estiver ali, `GET /api/plugins/vendas_ia/settings` **devolve a senha em claro** para quem tem `plugins.manage` — e isso o mascaramento da trilha não conserta.

| Sev. | Segunda lacuna |
|---|---|
| média | **"Semear agentes + tools"** ([routes.py:89](../storages/plugins/vendas_ia/routes.py#L89)): cria os agentes, insere 3 tools de **código** em `ai_tools` e liga o kill-switch global `ai_tools_code_enabled` — nada registrado. Ação: `vendas_ia.agentes.seed` com `{criados, pulados, force_router, tools_criadas}`; a linha do kill-switch só quando ele **de fato** virar. |

**Status:** ⬜

### 4.2 `protocolos` — 12 lacunas (o maior do parque)

Todas seguem o molde que o próprio plugin já usa em `_audit_field_defs` ([routes.py:744-777](../storages/plugins/protocolos/routes.py#L744)).

| Sev. | Ação sem trilha | Linha |
|---|---|---|
| **alta** | Salvar a regra **"Ignorar abertura por regex"** (liga/desliga, regex, direção) | [:806](../storages/plugins/protocolos/routes.py#L806) |
| média | Salvar as **mensagens de protocolo/avaliação** enviadas ao cliente (títulos e **links** normal/privado) | [:784](../storages/plugins/protocolos/routes.py#L784) |
| média | Salvar as **opções gerais** (auto-atribuir, posse temporária da IA, religar IA ao fechar, janela do popup) | [:794](../storages/plugins/protocolos/routes.py#L794) |
| média | Atendente **resolve** um atendimento no popup (grava rótulos e fecha o ciclo) | [:690](../storages/plugins/protocolos/routes.py#L690) |
| média | Atendente **finaliza** o protocolo (dispara mensagem ao cliente e religa a IA) | [:335](../storages/plugins/protocolos/routes.py#L335) |
| média | **"Faz parte do protocolo anterior"** — o protocolo novo é **APAGADO** e seus ciclos absorvidos | [:384](../storages/plugins/protocolos/routes.py#L384) |
| média | Excluir uma **visualização do Kanban**, inclusive de **equipe** (some para todos) | [:595](../storages/plugins/protocolos/routes.py#L595) |
| média | Editar os **rótulos do protocolo** ou arrastar o card entre colunas | [:320](../storages/plugins/protocolos/routes.py#L320) |
| média | Decisões do **popup de continuidade** ("novo protocolo", "fechar tudo") | [:429](../storages/plugins/protocolos/routes.py#L429) |
| baixa | Arrastar o card entre **colunas de atendente** (define o dono e propaga) | [:452](../storages/plugins/protocolos/routes.py#L452) |
| baixa | Criar/editar visualização do Kanban (agrupamento, filtros, **ACL de quem vê**) | [:564](../storages/plugins/protocolos/routes.py#L564) |
| baixa | **Reabrir** protocolo finalizado | [:376](../storages/plugins/protocolos/routes.py#L376) |

⚠️ Contraste que evidencia a assimetria: em produção o plugin registra `protocolos.protocolo.campos` (40 linhas) e `protocolos.atendimento.resolve` (19) com ator **`ai`** — a IA fechando protocolo deixa rastro; **o atendente humano fazendo o mesmo, não**. As três rotas de configuração e as rotas humanas estão sem cobertura.

**Status:** ⬜

### 4.3 `melhorias` — 6 lacunas (o plugin que edita o core)

| Sev. | O que falta | Linha |
|---|---|---|
| média | **Humano aprova** ✓ uma mutação da IA em agente/prompt/tool do core: há a linha da *escrita*, não a da **autorização**. Gravar `mutacao.aprovada` ao lado da `mutacao.recusada` que já existe | [routes.py:501](../storages/plugins/melhorias/routes.py#L501) |
| média | **Sênior aprova na fila de escalonamento** e o gateway **reaplica** a mutação — o ator sênior se perde | [replay.py:88](../storages/plugins/melhorias/replay.py#L88) |
| média | **Sênior recusa** uma mutação já aprovada por quem não tinha a chave | [routes.py:545](../storages/plugins/melhorias/routes.py#L545) |
| **média** ⚠️ | **`db_write` alcança a própria `audit_log`**: aprovar manda o executor agêntico externo (:8015) rodar **SQL arbitrário** no banco com o próprio papel Postgres — inclusive `UPDATE`/`DELETE` em `audit_log`. A única linha sobre a escrita é gravada pelo app e **pode ser apagada pelo SQL que ela registra** | [routes.py:509](../storages/plugins/melhorias/routes.py#L509) |
| baixa | A linha `db_write.aplicado` é gravada **no instante do clique**, antes de qualquer confirmação de que o executor rodou — a trilha afirma uma escrita que pode nunca ter acontecido | [routes.py:509](../storages/plugins/melhorias/routes.py#L509) |
| baixa | Redefinir o **filtro padrão compartilhado** do painel de sugestões | [routes.py:727](../storages/plugins/melhorias/routes.py#L727) |

**Correção do item de integridade, em duas camadas:** (1) conferir `\dp audit_log` no banco de produção com o papel real do executor e **revogar `UPDATE`/`DELETE`** se estiverem lá; (2) no ponto onde `needs_db_write_key` já exige a chave extra, **recusar fail-closed** todo SQL cujo alvo case `audit_log`, registrando a recusa como `melhorias.db_write.recusado`.

**Status:** ⬜

### 4.4 `retornos` — 3 lacunas

O plugin já audita `configuracao.*` (6 linhas em produção). Ficaram de fora, com o mesmo peso:

| Sev. | Ação | Linha |
|---|---|---|
| média | Editar um **retorno** (a régua: árvore E/OU, atrasos, limite de disparos, agente) | [routes.py:267](../storages/plugins/retornos/routes.py#L267) |
| média | Criar/editar/excluir uma **mensagem** do retorno — o texto que o robô manda sozinho ao cliente | [routes.py:311](../storages/plugins/retornos/routes.py#L311) |
| baixa | **Reordenar** retornos ou mensagens (a ordem define qual sai primeiro) — enquanto `reorder_configuracoes` [:128](../storages/plugins/retornos/routes.py#L128) **é** auditado | [:252](../storages/plugins/retornos/routes.py#L252) e [:285](../storages/plugins/retornos/routes.py#L285) |

**Status:** ⬜

### 4.5 `janela_72h` — configuração inteira sem trilha

| Sev. | O que acontece |
|---|---|
| **alta** | Salvar a tela "Configurar" — **token e app secret da Meta**, etiqueta usada, intervalo do sweep e os toggles de gravar atributo / mandar evento ao Trackify / deixar nota privada — não registra nada ([routes.py:69](../storages/plugins/janela_72h/routes.py#L69)). |

O plugin **já tem** um `_mask` pronto ([routes.py:36-46](../storages/plugins/janela_72h/routes.py#L36)) que zera o segredo e deixa só `<chave>_set`/`<chave>_hint` — que é exatamente o diff que interessa ("o token mudou: sim/não"). Ler o cfg antes com `force=True`, salvar, e auditar `janela_72h.config.update` com `before=_mask(antes)`, `after=_mask(depois)`. **Nunca o cfg cru.**

**Status:** ⬜

### 4.6 `utm_atendente` — o mapa que atribui comissão

| Sev. | O que acontece |
|---|---|
| média | Salvar o mapa **atendente → `utm_term`** (é o que atribui a comissão dos links de venda), mais o toggle e a regex de domínios, não registra nada ([routes.py:83](../storages/plugins/utm_atendente/routes.py#L83)). |

⚠️ Ler o `before` **dentro do mesmo `_persist`** ([routes.py:158-169](../storages/plugins/utm_atendente/routes.py#L158)) — uma só ida ao thread e sem janela de corrida entre ler e gravar. São ids e slugs: nada a mascarar.

**Status:** ⬜

### 4.7 `debug_bus` — captura e exportação de conteúdo de conversa

| Sev. | Ação | Linha |
|---|---|---|
| média | **Ligar a captura**: o sistema passa a **persistir** conteúdo de conversa, system prompt e histórico mandado ao LLM numa tabela — sem registrar quem ligou | [routes.py:59](../storages/plugins/debug_bus/routes.py#L59) |
| média | **Baixar o JSONL**: exporta para fora tudo que foi capturado (telefones, texto, payloads crus, prompts) — sem registrar quem baixou | [routes.py:78](../storages/plugins/debug_bus/routes.py#L78) |

O download é a exceção deliberada ao "GET não audita" da §4 do guia: revelar dado sensível em massa registra **quem viu**.

**Status:** ⬜

### 4.8 `agendamento_retorno` e `trackify` — uma cada

| Plugin | Sev. | Ação | Linha |
|---|---|---|---|
| `agendamento_retorno` | média | **Excluir um agendamento** (inclusive encerrado, o que exige permissão dedicada) apaga a linha fisicamente, sem rastro | [routes.py:72](../storages/plugins/agendamento_retorno/routes.py#L72) |
| `trackify` | baixa | Trocar a **chave de ingestão** e trocar a **chave da API** produzem linhas **idênticas e indistinguíveis**; e apagar a chave de ingestão (que derruba o espelho) parece uma gravação qualquer | [routes.py:207](../storages/plugins/trackify/routes.py#L207) e [:326](../storages/plugins/trackify/routes.py#L326) |

No `trackify`, separar por `resource_id` (`api_key` × `sync_api_key`) é o que faz o filtro da tela contar duas histórias, e registrar `{"chave_definida": bool(...), "inalterada": ...}` faz o save no-op parar de mentir.

**Status:** ⬜

### 4.9 `instagram` — a ação fica, o "quem" some (⚠️ depende do 147)

| Sev. | O que acontece |
|---|---|
| baixa | "Conectar com Instagram" grava `instagram.conta.conectar` — **com ator `system`**, sem usuário, IP ou `request_id` ([routes.py:614](../storages/plugins/instagram/routes.py#L614)). O callback é uma navegação de topo vinda da Meta, que legitimamente não carrega o Bearer do painel. |

Duas saídas:

- **Barata, sem core, sem migration** (recomendada se o 147 atrasar): auditar **no início**, na rota `/oauth/start` ([routes.py:416](../storages/plugins/instagram/routes.py#L416)) — ali o `ContextVar` já tem o usuário real. A correlação com o `conta.conectar` do callback fica pelo canal + janela de minutos.
- **Completa**: colunas `actor_*` em `plugin_instagram_oauth_state` + o seam de ator que o **plano 147 item 60** entrega. Custa migration 003. Só vale se a correlação por tempo se mostrar insuficiente.

**Status:** ⬜

### 4.10 `gowa` — ⚠️ a fonte é `assets/`, não `storages/`

| Sev. | Ação | Linha |
|---|---|---|
| baixa | **"Testar alerta"**: quando o grupo do Telegram virou supergrupo, a rota **grava o novo `chat_id`** (para onde os alertas passam a ir) sem trilha — e a última linha `gowa.alerta.config` continua mostrando o chat antigo | [routes.py:204](../assets/plugin_examples/gowa/routes.py#L204) |
| baixa | **Abrir a aba "Configurar"**: o `GET` grava o fuso detectado do navegador na config do plugin, mudando o horário dos alertas, sem trilha e sem o usuário ter salvado nada | [routes.py:107](../assets/plugin_examples/gowa/routes.py#L107) |

⚠️ **A correção do segundo é de DESENHO, não de auditoria**: tirar a escrita do `GET` e mandar o `tz` no `PUT` — que **já é auditado**. A tela já preenche `cfg.timezone` a partir de `timezone_auto` no load e já envia `timezone` no save, então o valor passa a entrar pelo caminho auditado **sem UI nova**. Auditar a cada abertura de tela transformaria a trilha em log de navegação.
Em qualquer variante, acrescentar `timezone_auto` ao `_alert_audit_view()` — hoje o diff do `PUT` esconde o fuso efetivo. **Mesma mudança no gêmeo `whatsapp_cloud/routes.py:851/874`.**

🚫 Editar em `assets/plugin_examples/gowa/` — a cópia de `storages/` é sobrescrita no boot.

**Status:** ⬜

---

## 5 — Ordem de execução sugerida

| Tranche | Conteúdo | Por quê primeiro |
|---|---|---|
| **T0** | `vendas_ia` (a) e (b) — §4.1 | Vazamento **ativo** de credencial em produção |
| **T1** | `janela_72h`, `utm_atendente`, `agendamento_retorno`, `debug_bus`, `trackify` | Um call site cada, zips pequenos, risco baixo |
| **T2** | `retornos`, `melhorias` (inclusive o fail-closed do `audit_log`) | Médio porte, um `.zip` cada |
| **T3** | `protocolos` (12 itens) | Maior superfície, maior risco de publicação — sozinho |
| **T4** | `gowa` (+ gêmeo `whatsapp_cloud`) e `instagram` | `gowa` é bundled (fluxo diferente); `instagram` depende do 147 |

---

## 6 — Riscos e armadilhas

| # | Armadilha | Por que morde |
|---|---|---|
| R1 | Corrigir em `storages/plugins/<id>/` | É a cópia instalada e gitignorada. A correção some no redeploy; no `gowa`, no próximo bump |
| R2 | Publicar sem conferir o remoto e a tabela `plugins` de **produção** | Versão pode ter sido publicada direto no repositório de plugins, fora do seu clone — sobrescrever apaga o delta em silêncio |
| R3 | `--check` acusando "outdated" e você rebuildar | Pode ser só `umask` 664 × 644. Rebuildar é o caminho destrutivo |
| R4 | Auditar `GET` de tela | Vira log de navegação. A exceção é **só** revelar segredo / exportar em massa (§4.7) |
| R5 | Gravar o cfg cru no `after` | `janela_72h` e `trackify` carregam token e app secret. Use o `_mask` que já existe |
| R6 | Ação fora da regex `PLUGIN_ACTION_RE` | Descartada **com WARNING**, sem quebrar a rota — a lacuna volta em silêncio |
| R7 | Auditar **antes** do `return` de erro | Uma tentativa que falhou não é uma mudança |
| R8 | Import de `plugins.context` não defensivo | O plugin **não carrega** num core anterior — falha muda no boot |

---

## 7 — Testes

Os testes de plugin **não rodam** no pytest do core. Para cada plugin tocado:

```bash
cd ../whatsbot-pro-plugins && python3 scripts/test_plugins.py <id>
```

⚠️ O harness copia o plugin para `/tmp` mas **não muda o `cwd`** — ancore caminhos no pacote, nunca em `os.getcwd()`.

Por plugin, no mínimo: a rota audita no sucesso; **não** audita no erro; o segredo não aparece no `after`; a ação casa `PLUGIN_ACTION_RE`.

---

## 8 — Documentação

- [docs/PLUGINS_AUDITAVEIS.md](../docs/PLUGINS_AUDITAVEIS.md): acrescentar à §4 o caso "**exportar em massa audita quem baixou**" (o JSONL do `debug_bus`), ao lado do `reveal-hmac` que já está lá; e à §6 o aprendizado do `nexus_dsn` — **uma chave de nome inocente passa em claro**, o mascaramento do core é rede de segurança, não licença.
- [docs/PLUGINS.md](../docs/PLUGINS.md): registrar a assimetria encontrada como regra — *auditar a configuração e não auditar a ação de estado é o defeito mais comum do parque*.

---

## 9 — Execução (2026-08-31)

As 32 lacunas fechadas, mais os dois ajustes de core previstos em §0 (D3/D4) —
já resolvidos pelo plano 147/149 antes desta frente começar. §8 foi cumprido:
os dois documentos citados ganharam as seções descritas.

| Plugin | Lacunas fechadas | Versão | Testes |
|---|---|---|---|
| `protocolos` | 12 (o maior do parque) | 2.7.0 → **2.8.0** | 295 |
| `melhorias` | 6 (inclui reforço do guard fail-closed do `audit_log`) | 1.12.0 → **1.13.0** | 125 |
| `retornos` | 6 (CRUD completo de configuração/retorno/mensagem + controle) | 1.20.1 → **1.21.0** | 111 |
| `vendas_ia` | 2 (semear agentes, ligar tools de código) + a correção urgente do §4.1 (via 149·F8) | 1.9.0 → **1.10.0** | 179 |
| `janela_72h` | 1 | 1.5.0 → **1.6.0** | 91 |
| `agendamento_retorno` | 1 | 1.6.0 → **1.7.0** | 81 |
| `trackify` | 2 (+ correção R8 "cinto sem calça": import defensivo era decorativo em 3 módulos) | 4.0.0 → **4.1.0** | 266 |
| `instagram` | 1 (alternativa barata do §4.9 — não esperou o 147) | 3.3.0 → **3.4.0** | 75 |
| `utm_atendente` | 1 | 1.0.0 → **1.1.0** | 60 |
| `debug_bus` | 3 (captura, limpar depósito, exportar) | 1.1.0 → **1.2.0** | 14 (suíte nova) |
| `gowa` + `whatsapp_cloud` | 1 cada (migração de chat_id de alerta) + vazamento do token do Telegram no log de erro (`gowa`) | 1.3.1→**1.4.0** / 1.12.0→**1.13.0** | 15 / 319 |

D5 (§0): `protocolos` foi tranche própria, como o plano previa.

### Release

`catalog.json` e os 12 `<id>.json` sincronizados com a versão/descrição de
cada `src/plugin.yaml`; `build_plugins.py --all --check` limpo nos 27 plugins
do catálogo (nada regrediu fora do escopo). `gowa` foi bumpado nas **duas**
cópias-fonte (`../whatsbot-pro-plugins/plugins/gowa/src/` e
`assets/plugin_examples/gowa/` no core) — R1/§6 exige as duas, senão o bundled
não atualiza. Publicado **localmente**: os 12 zips foram extraídos sobre
`storages/plugins/<id>/`; a tabela `plugins` confirma os 12 na versão nova,
`enabled=1`, `load_error=None` — o hot-reload do dev server (`--reload-dir
storages/plugins`) recarregou sem erro. **Não foi feito `git push`** para o
repositório de plugins nem import em produção.
