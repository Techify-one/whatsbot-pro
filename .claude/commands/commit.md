Faça um release do WhatsBot seguindo estes passos exatos:

1. Leia o arquivo VERSION na raiz do projeto
2. Incremente a versão patch (ex: 1.0.0 → 1.0.1, 1.2.3 → 1.2.4)
3. Salve o novo número no arquivo VERSION
4. Faça git add de TODOS os arquivos modificados e não rastreados (exceto .env, storages/, logs/, venv/, __pycache__)
5. Crie um commit com a mensagem: "release: v{nova_versão}" seguido de uma linha em branco e um resumo curto das mudanças desde o último commit de release (use git log)
6. Push para origin (franc-nt) e upstream (Techify-one) na branch main

Se o argumento passado for "minor", incremente o minor (ex: 1.0.1 → 1.1.0).
Se o argumento passado for "major", incremente o major (ex: 1.1.0 → 2.0.0).
Sem argumento, incrementa o patch.

Argumento recebido: $ARGUMENTS
