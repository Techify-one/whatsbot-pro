Crie uma nova release do WhatsBot no GitHub seguindo estes passos:

1. Descubra a versão atual pela última tag git: `git describe --tags --abbrev=0 --match "v*"` (se não houver tag, considere v0.0.0)
2. Incremente a versão patch (ex: 0.1.0 → 0.1.1). Se o argumento for "minor", incremente o minor (ex: 0.1.1 → 0.2.0). Se for "major", incremente o major (ex: 0.2.0 → 1.0.0)
3. Rode `git status` e `git diff` para ver se há mudanças não commitadas
4. Se houver mudanças pendentes, faça commit primeiro (git add + commit com mensagem descritiva)
5. Push para origin e upstream na branch main
6. Gere o changelog automático: liste os commits desde a última tag de release (`git log <última_tag>..HEAD --oneline --no-merges`). Se não houver tag anterior, use os últimos 20 commits
7. Crie a tag e o GitHub Release usando `gh release create v{nova_versão} --target main --title "v{nova_versão}" --notes "<changelog formatado>"`
8. Mostre o link do release criado

Formato do changelog nas notes:
```
## O que mudou

- descrição do commit 1
- descrição do commit 2
...
```

Argumento recebido: $ARGUMENTS
