# Plano 23 · Sub-plano 04 — Dados, redundâncias & tooling

> Parte do [Plano 23 — Mestre](23-plano-refatoracao-00-mestre.md).

## 5. Mapa de redundâncias (consolidação por helper)

Ordenado por risco. ✅ = baixo risco/alto retorno (Wave 0/1).

| # | Duplicação | Cópias | Consolidar em | Risco |
|---|---|---:|---|---|
| R1 ✅ | `formatPhoneDisplay` (2 variantes divergentes) | 8 | `utils/phone.js` | baixo |
| R2 ✅ | wrapper 401 multipart (api.js) | 2 | `services/httpClient.uploadRequest()` | baixo |
| R3 ✅ | card erro `role:'error'` + try GOWASendError | 11/7 | `server/helpers/error_bubble.py` | baixo |
| R4 ✅ | prefixo transcrição `[Transcrição]/[Descrição]/[Conteúdo]` | 3 | `server/transcription.format_media_content()` | baixo |
| R5 ✅ | cerimônia "aplica filter → re-extrai 6 campos" | 4 | `apply_message_filter()` helper | baixo |
| R6 ✅ | ack_phone multi-campo | 3 | `channels/jid.phone_from_ack_payload()` | baixo |
| R7 ✅ | usage `by_type` shape + N+1 | 3 | `usage_repo._shape_by_type()` + GROUP BY | baixo |
| R8 ✅ | harness `check()/section()` (3 assinaturas) | 15 | pytest / `tests/conftest.py` | baixo |
| R9 ✅ | `--reload-dir` / GOWA_VERSION (launchers) | 3-4 | `scripts/_common` | baixo |
| R10b ✅ | `coerce_json` / `row→dict` | 9/6 | `db/repositories/_mapping.py` | baixo |
| R-aud ✅ | `AUDITABLE_EVENTS` nomes divergentes (`toggle_ai` vs `ai_toggled`, `tag.create` vs `tag.created`) | — | reconciliar c/ nomes de bus | baixo |
| R-bc | `_broadcast(deps, ws_event, bus_event, conv)` (já É broadcast+emit) | — | **lift/generalizar** p/ `messaging_service.broadcast_and_emit` (NÃO greenfield) | baixo |
| R11 | media preview (contact/conversation repo) | 2 | `_mapping.media_preview()` | médio |
| R12 | dedup mensagem optimistic (front) | 2 | `services/messages.js` | médio |
| R13 | conversation patch WS (Contacts↔Attendances) | — | `services/conversationPatch.js` | médio |
| R14 | handlers de mídia send | 3 | `messaging_service._send_media()` | médio |
| R15 | provider instantiation `try TypeError fallback` | 4 | `ChannelRegistry.instantiate()` | médio |
| R16 | `permission_denied + get+404` | ~50 | `Depends(require_permission)` + `get_channel_or_404` | **médio (muda authz!)** |
| R17 | config keys espelhadas (DEFAULT_CONFIG/GET/PUT) | — | metadados por-chave em `settings.py` | médio |
| R20 | "resolver conversa do contato + emit notice" | 3 | `system_notices.emit_for_contact()` | médio |
| R10t | `JSONText` TypeDecorator (reactions/model_config/channels.config) | — | **CORTADO** (ver §6 E1) — só se surgir bug concreto, e **sem Alembic** | — |
| R18 | sync LLM loop | — | **migrar callers** (sandbox/improvement) → depois remover (NÃO "dead code") | alto |
| R19 | parsing GOWA + fan-out (1 morta-divergente) | 3 | deletar legado + `event_actions_service` — **após spike de medição** | alto |

---

## Fases (workstreams A-tooling, G, E)

### WAVE 0

#### Fase A0 — pytest + harness único + CI 🔴
- **Objetivo:** runner real, gate de PR, harness único. Pré-requisito de **todas** as caracterizações.
- **Arquivos:** `pyproject.toml`/`pytest.ini`, `tests/conftest.py` (fixtures `app`/`client`/`seed`/`tmp data_dir`), `tests/_harness.py` (transição `check()→assert`), `.github/workflows/tests.yml` (matrix SQLite + Postgres service). App **hermético**: `data_dir`/`plugins_dir` → tmp determinístico (hoje lê `storages/plugins` real). `TestClient(raise_server_exceptions=True)`.
- **Inclui:** **CI de hygiene Alembic** (script: `alembic heads` deve dar 1; checa **prefixo duplicado** — já há `0021_inbox_members` + `0021_template_permissions`). Barato e protege TODAS as migrations futuras.
- **Inclui:** decidir runner JS = `node --test` sobre módulos puros ESM.
- **Risco:** baixo. 🟢 com A1, G0.

#### Fase A1 — `tests/fakes.py` + dividir `test_endpoints.py` 🟢 (com A0)
- **Objetivo:** mover 13 `_Fake*` p/ `tests/fakes.py` público; quebrar 3805 em `tests/endpoints/test_<domínio>.py`.
- **Arquivos:** 13 módulos (auth_config, contacts, tags, conversations, rbac, audit, system_notices, channels, webhook, cloud_templates, telegram, misc) + `tests/fakes.py`.
- **Risco:** baixo.

#### Fase G0 — Launchers + pin de deps + dead-config 🟢 (com A0)
- **Objetivo:** R9; pin minor-safe (fastapi/uvicorn/httpx/pyyaml/python-multipart) + `requirements.lock`. Remover `version:` obsoleto do compose, healthcheck duplicado, `Settings.save()` no-op, `batch_tasks` legado.
- **Arquivos:** launchers, `Dockerfile`, `docker-compose.yaml`, `requirements.txt`, `config/settings.py`, `server/state.py`.
- **Risco:** baixo.

#### Fase G1-mínimo — `build_test_app(plugins=[...])` 🟢
- **Objetivo:** antecipar a parte mínima das fixtures de plugin (rascunho punha em Wave 5) — destrava caracterizações que sobem o app com o canal `default` (F2/B3) e o lifecycle de plugin (C4).
- **Risco:** baixo.

### WAVE 1

#### Fase E1 — `db/repositories/_mapping.py` + mover catálogo 🔴
- **Objetivo:** R10b/R11 — `row_to_dict`/`coerce_json`/`media_preview` central. `get_or_create` relê e retorna `_row_to_dict` (corrige shape divergente). Mover `PERMISSION_CATALOG` → `domain/permission_catalog.py` (quebra ciclo db→server).
- **CORTADO do escopo:** `JSONText` TypeDecorator + migração de coluna (R10t). O ganho real é matar `coerce_json` ×9 = um helper. TypeDecorator muda semântica read/write em toda query + risco em `migration_postgres.py` (cópia cross-backend) — **desproporcional**. Reintroduzir **só** se surgir bug concreto (double-encode), e **sem Alembic** (decorator envolve TEXT existente sem schema change). Caracterizar shape de leitura/escrita de `channels.config` (plugins leem cru) antes.
- **Caracterização antes:** shape de saída de cada repo tocado.
- **Risco:** médio.

### WAVE 5 — Repos/DB profundos + limpeza final

#### Fase E2 — Decompor `contact_repo.py` (693) + `conversation_repo.py` 🟢
- **Objetivo:** `unread_repo`, `observation_repo`, `db/search/contact_search.py`, `contact_query`/`conversation_query`; SQL `.format()` → Core; matar N+1 de tags; eleger fonte única de unread (conversa-cêntrico) e remover `unread_conversation_count` morto. `_br_phone_variants` → `channels/br_phone.py`.
- **Caracterização antes:** repo (list_contacts, search, unread).
- **Risco:** médio.

#### Fase E3 — FK / Alembic estrutural 🔴
- **Objetivo:** reconciliar `tables.py` ↔ schema real (FK `messages.conversation_id` → CASCADE). (O CI de linearidade/prefixo **já foi p/ A0**.) **Round-trip test** SQLite→PG via `migration_postgres.py` p/ qualquer mudança de coluna.
- **Risco:** médio (migration).

#### Fase G2 — Fixtures de teste para plugins (completo) 🟢
- **Objetivo:** completar `tests/support` (além do G1-mínimo): `testpaths` descobre `storages/plugins/<id>/tests/`. Permite o plugin atendimento testar rotas/filters contra app real.
- **Risco:** baixo.
