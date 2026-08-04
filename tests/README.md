# Testes do WhatsBot Pro

Esta pasta contém somente testes do core e dos contratos que o core oferece aos
plugins. Testes de comportamento de um plugin vivem no repositório
`whatsbot-pro-plugins`, ao lado da fonte desse plugin.

## Estrutura

```text
tests/
  core/          unidades e caracterização interna do core
  contracts/     contratos públicos usados por plugins
  integration/   API, Postgres e costuras entre componentes do core
```

As suítes antigas de script ficam temporariamente em `core/legacy/` e são
executadas pelo teste `core/test_legacy_scripts.py`. Arquivos auxiliares e
fixtures compartilhadas ficam diretamente em `tests/`, fora da descoberta do
pytest.

## Executar

```bash
# Toda a suíte configurada em pyproject.toml
./venv/bin/python -m pytest

# Uma camada
./venv/bin/python -m pytest tests/contracts
```

Testes que usam banco exigem `WHATSBOT_TEST_DB_URL`. O helper `tests/pg.py`
recusa por padrão bancos cujo nome não contenha `test` e recria o schema
`public`, portanto não execute duas suítes PostgreSQL contra o mesmo banco em
paralelo.

## Testes de plugins

Instalar, atualizar, ativar ou iniciar um plugin em produção não executa
testes. O core também não procura testes em `storages/plugins/`.

No checkout irmão de plugins, use um comando explícito:

```bash
cd ../whatsbot-pro-plugins
python3 scripts/test_plugins.py protocolos
python3 scripts/test_plugins.py --all
```

Nomeie arquivos e funções pelo comportamento protegido, por exemplo
`test_keep_assignee_on_close.py`. Não use números de plano como identidade do
teste; o plano pode aparecer no histórico ou na explicação quando for útil.
