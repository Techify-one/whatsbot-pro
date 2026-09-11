Faça commit e push do WhatsBot seguindo estes passos:

1. Rode `git status` e `git diff` para ver todas as mudanças
2. Se o diff tocar `plugins/`, `channels/` ou `server/routes/`, rode o guard da API de plugins antes de seguir (2s, não precisa de banco):
   `venv/bin/python -m pytest tests/contracts/test_plugin_api_surface.py -q`
   Vermelho significa que a superfície da API mudou sem bump — a própria falha imprime os 3 passos (bump em `plugins/semver.py`, entrada em `docs/PLUGIN_API_CHANGELOG.md`, regenerar o snapshot). NÃO commite por cima.
3. Faça `git add` de TODOS os arquivos modificados e não rastreados (exceto .env, storages/, logs/, venv/, __pycache__)
4. Analise as mudanças e crie uma mensagem de commit descritiva seguindo o padrão conventional commits (feat, fix, refactor, docs, etc.)
5. Push para o repositório:
   - https://github.com/Techify-one/whatsbot

NÃO crie releases ou tags. Para releases, use /release-up.

Argumento opcional (mensagem de commit personalizada): $ARGUMENTS
