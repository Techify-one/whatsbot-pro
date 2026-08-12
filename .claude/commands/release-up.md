Crie uma nova release do WhatsBot no GitHub seguindo estes passos:

1. Descubra a versão atual com `gh release list --limit 1` — **não** use `git describe --tags`.
   ⚠️ As tags locais antigas (`v0.0.1`…`v0.1.1`) apontam para commits **órfãos**: a reescrita de histórico de 2026-07-23 trocou todos os hashes, e `git merge-base <tag> main` devolve vazio. `git describe` acha essas tags e mente sobre a versão, e um intervalo `<tag>..main` não significa nada. A fonte de verdade é a lista de releases do GitHub.
2. Incremente a versão patch (ex: 0.7.0 → 0.7.1). Se o argumento for "minor", incremente o minor; se for "major", o major.
3. Rode `git status` e `git diff` para ver se há mudanças não commitadas.
4. Se houver mudanças pendentes, faça commit primeiro (git add + commit com mensagem descritiva).
5. Push para **`origin`** na branch main. (Não existe remote `upstream` neste checkout — conferir com `git remote -v` antes de inventar um.)
6. Escreva a nota **à mão**, em PT-BR e em linguagem de produto, para quem **usa** o WhatsBot: o que mudou na prática, e uma seção "Ações de atualização" quando houver migração, mudança que quebra ou passo manual.
   ⚠️ **Não** use `--generate-notes`: o GitHub monta essa nota a partir de *pull requests*, e este repositório quase não os usa — sairia vazia.
   Use `git log <commit_da_última_release>..HEAD --oneline --no-merges` só como **matéria-prima**, nunca como a nota em si.
7. Crie a release **como rascunho** primeiro — rascunho não notifica ninguém e não cria a tag remota, então dá para corrigir:
   ```bash
   gh release create v{nova_versão} --draft --title "v{nova_versão} — {título curto}" --notes-file nota.md
   ```
8. Revise o rascunho no GitHub e só então publique.
9. Ao publicar, seja **explícito** sobre o "Latest":
   - `--latest` na release mais nova;
   - `--latest=false` em qualquer release **retroativa** (que aponte para um commit antigo). O `gh` só envia `make_latest` quando o flag é passado; omitido, vale o default da API REST, que é **`true`** — e uma release de junho viraria "a atual".
10. Mostre o link do release retornado pelo `gh`.

Argumento recebido: $ARGUMENTS
