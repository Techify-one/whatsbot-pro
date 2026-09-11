"""Higiene do ``CLAUDE.md``: teto de tamanho e prova de que nada foi perdido.

Contexto (plano 139). O ``CLAUDE.md`` é lido em TODA requisição de TODA sessão
do Claude Code. Ele passou o teto de 150 000 chars da ferramenta crescendo
~2 500 chars/dia — todo plano executado acrescentava um parágrafo e nada saía.
O conserto foi mover a NARRATIVA para guias temáticos em ``docs/`` e deixar no
``CLAUDE.md`` a regra + o tripwire + o link.

Estes dois testes são o que impede a recaída:

* ``test_claude_md_size`` — teto do PROJETO (mais apertado que o da ferramenta,
  de propósito: no limite de 150k o teste só acusaria quando já é tarde, e daria
  a impressão de que 149k está "ok" — 149k já custa ~47k tokens por turno).
* ``test_claude_md_facts_preserved`` — cada token, caminho, tripwire e chave de
  tabela que existia antes do corte continua existindo em ``CLAUDE.md`` ∪
  ``docs/``. É o que torna "não perdi informação" verificável.

⚠️ Quando o primeiro ficar vermelho, NÃO aumente ``LIMITE``. Mova a narrativa
para o guia de ``docs/`` da área e deixe no ``CLAUDE.md`` a regra + o ⚠️ + o
link. O teto subir é exatamente o modo de falha que este teste existe para pegar.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Carregado por CAMINHO, de propósito: ``scripts/`` não é pacote e um
# ``sys.path.insert`` aqui valeria para a sessão inteira do pytest, deixando o
# diretório na frente do projeto para todo import subsequente. Nenhum nome em
# ``scripts/`` colide hoje, mas o próximo a ser criado não deve poder quebrar a
# suíte por causa deste teste de documentação.
_spec = importlib.util.spec_from_file_location("_docs_facts", ROOT / "scripts" / "docs_facts.py")
docs_facts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(docs_facts)

# Teto do projeto. O do Claude Code é 150_000; ficamos bem abaixo para ter
# espaço de crescimento sem voltar ao problema em poucas semanas.
LIMITE = 90_000


def test_claude_md_size() -> None:
    n = len((ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
    assert n <= LIMITE, (
        f"CLAUDE.md tem {n} chars (teto do projeto: {LIMITE}). "
        "NÃO aumente o teto: mova a narrativa para o guia de docs/ da área e "
        "deixe aqui a regra + o ⚠️ + o link (plano 139 §4)."
    )


def test_claude_md_facts_preserved() -> None:
    missing = docs_facts.check()
    amostra = "\n".join(f"  [{c}] {f[:140]}" for c, f in missing[:20])
    assert not missing, (
        f"{len(missing)} fatos sumiram de CLAUDE.md ∪ docs/:\n{amostra}\n"
        "Documentação do WhatsBot não se apaga, se move: leve o trecho para o "
        "guia de docs/ correspondente (plano 139)."
    )
