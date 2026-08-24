#!/usr/bin/env python3
"""Prova mecânica de que a reorganização da documentação não perdeu informação.

O ``CLAUDE.md`` cresceu até passar o teto de contexto do Claude Code (plano 139).
A solução foi mover a NARRATIVA para guias temáticos em ``docs/`` e deixar no
``CLAUDE.md`` a regra + o tripwire + o link. Este script existe para transformar
"não perdi nada" numa verificação, em vez de uma promessa.

Modos
-----
``--snapshot [ref]``
    Extrai o inventário de **fatos atômicos** de uma revisão do ``CLAUDE.md``
    (default: o commit anterior ao corte, fixado em ``docs/.facts.json``) e grava
    o golden. Rode isto UMA vez, ANTES do corte. Regenerar depois só com
    justificativa no commit — senão o golden passa a mentir.

``--check``
    Verifica que cada fato do golden continua existindo em ``CLAUDE.md`` ∪
    ``docs/**/*.md``. É o portão de cada fase do plano 139.

``--audit-paths``
    Informativo: lista caminhos de arquivo citados que não existem no disco.
    Não falha (muitos são do repositório irmão ``whatsbot-pro-plugins``).

O que conta como "fato atômico"
-------------------------------
- todo token entre crases (identificador, chave de config, nome de evento/filtro,
  endpoint) — é o vocabulário que uma sessão futura vai procurar;
- todo caminho de arquivo citado;
- toda linha com ⚠️ ou 🚫 — os tripwires, o conteúdo de maior valor do arquivo;
- a primeira célula de cada linha de tabela — a chave de cada catálogo.

⚠️ O check compara PRESENÇA DE TOKEN, não prosa. Ele prova que nada sumiu; não
prova que o texto continua bom. A revisão humana continua obrigatória.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "docs" / ".facts.json"
CLAUDE_MD = ROOT / "CLAUDE.md"

# Revisão de referência: o último commit antes do corte do plano 139.
DEFAULT_REF = "921da9f"

_BACKTICK = re.compile(r"`([^`\n]{2,80})`")
_PATH = re.compile(
    r"(?:[\w.\-]+/)+[\w.\-]+\.(?:py|js|md|sql|ya?ml|html|css|json|sh|bat|command|exe)"
)
_TRIPWIRE = re.compile(r"⚠️|🚫")
_TABLE_SEP = re.compile(r"^\|[\s:|-]+\|?$")


def _strip_md(cell: str) -> str:
    """Chave estável de uma célula de tabela: sem negrito, sem link, sem crase."""
    cell = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cell)
    return cell.replace("*", "").replace("`", "").replace("~", "").strip()


def extract(text: str) -> dict[str, list[str]]:
    lines = text.split("\n")
    tokens = set(_BACKTICK.findall(text))
    paths = set(_PATH.findall(text))
    tripwires, table_keys = [], set()
    in_code = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if _TRIPWIRE.search(line):
            tripwires.append(line.strip())
        if not in_code and line.startswith("|") and not _TABLE_SEP.match(line):
            key = _strip_md(line.split("|")[1] if "|" in line[1:] else "")
            if key and key.lower() not in {"", "#", "método", "endpoint", "evento",
                                           "filter", "tabela", "linhas", "seção"}:
                table_keys.add(key)
    return {
        "tokens": sorted(tokens),
        "paths": sorted(paths),
        "tripwires": tripwires,
        "table_keys": sorted(table_keys),
    }


def _haystack() -> str:
    parts = [CLAUDE_MD.read_text(encoding="utf-8")]
    for p in sorted((ROOT / "docs").rglob("*.md")):
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def cmd_snapshot(ref: str) -> int:
    if ref == "-":
        text = CLAUDE_MD.read_text(encoding="utf-8")
        origin = "working tree"
    else:
        text = subprocess.run(
            ["git", "show", f"{ref}:CLAUDE.md"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        ).stdout
        origin = ref
    facts = extract(text)
    facts["_origin"] = origin
    facts["_source_chars"] = len(text)
    GOLDEN.write_text(json.dumps(facts, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"snapshot de {origin} ({len(text)} chars) → {GOLDEN.relative_to(ROOT)}")
    for k in ("tokens", "paths", "tripwires", "table_keys"):
        print(f"  {k:12s} {len(facts[k])}")
    return 0


def check() -> list[tuple[str, str]]:
    """Devolve a lista de (categoria, fato) que sumiu. Vazia = tudo preservado."""
    facts = json.loads(GOLDEN.read_text(encoding="utf-8"))
    hay = _haystack()
    # A chave de tabela é comparada SEM markup: ela foi extraída já normalizada
    # (`x` / **x** viram x), então o haystack precisa da mesma normalização —
    # senão uma célula como `` `ai_agents` / `ai_variables` `` nunca casaria.
    hay_norm = hay.replace("`", "").replace("*", "")
    # Um link que sai do CLAUDE.md (raiz) para um guia de docs/ ganha o ``../``
    # que faz ele resolver de dentro da subpasta — senão os 217 links de código
    # dos guias apontariam para ``docs/server/app.py`` e dariam 404 no GitHub.
    # O reancoramento é de ENDEREÇO, não de conteúdo, então o comparador o
    # desfaz antes de procurar o fato: exigir a linha byte a byte obrigaria a
    # escolher entre o guard verde e o link que funciona.
    hay_rooted = hay.replace("](../", "](")

    def presente(fact: str) -> bool:
        return fact in hay or fact in hay_rooted

    missing: list[tuple[str, str]] = []
    for cat in ("tokens", "paths"):
        for fact in facts[cat]:
            if not presente(fact):
                missing.append((cat, fact))
    for fact in facts["table_keys"]:
        if fact not in hay_norm and fact not in hay_rooted.replace("`", "").replace("*", ""):
            missing.append(("table_keys", fact))
    for line in facts["tripwires"]:
        # O tripwire pode ser reescrito curto no CLAUDE.md, mas a linha original
        # tem de sobreviver VERBATIM no guia que recebeu aquela seção.
        if not presente(line):
            missing.append(("tripwire", line))
    return missing


def cmd_check() -> int:
    missing = check()
    if not missing:
        facts = json.loads(GOLDEN.read_text(encoding="utf-8"))
        total = sum(len(facts[k]) for k in ("tokens", "paths", "tripwires", "table_keys"))
        print(f"OK — {total} fatos do golden ({facts['_origin']}) presentes em CLAUDE.md ∪ docs/")
        return 0
    print(f"FALHA — {len(missing)} fatos sumiram de CLAUDE.md ∪ docs/:\n", file=sys.stderr)
    for cat, fact in missing[:80]:
        print(f"  [{cat}] {fact[:160]}", file=sys.stderr)
    if len(missing) > 80:
        print(f"  … e mais {len(missing) - 80}", file=sys.stderr)
    print(
        "\nO conteúdo não pode ser apagado: mova-o para o guia de docs/ da área"
        " (plano 139 §4) ou traga-o de volta ao CLAUDE.md.",
        file=sys.stderr,
    )
    return 1


def cmd_audit_paths() -> int:
    facts = json.loads(GOLDEN.read_text(encoding="utf-8"))
    missing = [p for p in facts["paths"] if not (ROOT / p).exists()]
    print(f"{len(missing)} de {len(facts['paths'])} caminhos citados não existem no disco:")
    for p in missing:
        print("  ", p)
    print("\n(caminhos do repositório irmão whatsbot-pro-plugins são esperados aqui)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--snapshot", nargs="?", const=DEFAULT_REF, metavar="REF",
                   help=f"congela o golden a partir de <REF>:CLAUDE.md (default {DEFAULT_REF}; '-' = working tree)")
    g.add_argument("--check", action="store_true", help="verifica a cobertura dos fatos")
    g.add_argument("--audit-paths", action="store_true", help="lista caminhos citados inexistentes")
    a = ap.parse_args()
    if a.snapshot:
        return cmd_snapshot(a.snapshot)
    if a.check:
        return cmd_check()
    return cmd_audit_paths()


if __name__ == "__main__":
    raise SystemExit(main())
