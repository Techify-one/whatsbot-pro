"""Seed de contatos FALSOS para testar a paginação da tela Contatos (15 por página).

Idempotente — rodar de novo não duplica. NÃO faz parte do app (script de conveniência,
espelha o padrão do seed_demo.py da raiz).

Todos os contatos criados usam o prefixo de telefone ``551190000000`` (faixa fake) e
recebem a tag ``teste_paginacao``, então dá pra identificar e remover sem ambiguidade.

    ./venv/bin/python scripts/seed_test_contacts.py --yes       # cria 60 contatos
    ./venv/bin/python scripts/seed_test_contacts.py --remove --yes

Postgres-only (plano 29): usa a DATABASE_URL da env; se ausente, lê a linha
DATABASE_URL= do .env na raiz do repo (a mesma que o linux_start.sh carrega).
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if not os.environ.get("DATABASE_URL"):
    _envfile = ROOT / ".env"
    if _envfile.is_file():
        for _line in _envfile.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line.startswith("DATABASE_URL="):
                os.environ["DATABASE_URL"] = _line.split("=", 1)[1].strip().strip('"').strip("'")
                break

from sqlalchemy import select  # noqa: E402
from db.engine import init_engine, get_engine, resolve_database_url  # noqa: E402
from db.tables import contacts  # noqa: E402

# ── Dados dos contatos falsos ────────────────────────────────────────────────
PHONE_PREFIX = "551190000000"   # + 2 dígitos → 13 dígitos (formato BR completo)
TEST_TAG = "teste_paginacao"
TEST_TAG_COLOR = "#f59e0b"

# 60 nomes = 4 páginas cheias de 15. Cobrem o alfabeto de ponta a ponta porque a
# tela ordena por nome (sort=name) — assim dá pra ver a ordem virar entre páginas.
NAMES = [
    "Adriana Nogueira", "Alexandre Prado", "Aline Moura", "Amanda Ribeiro",
    "Anderson Faria", "Beatriz Camargo", "Bruno Antunes", "Camila Verissimo",
    "Carlos Eduardo Lima", "Cintia Barroso", "Daniel Fontes", "Débora Siqueira",
    "Diego Marinho", "Eduardo Rangel", "Elaine Pacheco", "Fábio Quintana",
    "Fernanda Bastos", "Filipe Andrade", "Gabriela Teixeira", "Gustavo Peixoto",
    "Helena Vasconcelos", "Henrique Sampaio", "Igor Bittencourt", "Isabela Cordeiro",
    "Jaqueline Muniz", "João Vitor Salles", "Juliana Braga", "Kelly Assunção",
    "Larissa Fontenele", "Leonardo Drummond", "Letícia Aragão", "Lucas Meireles",
    "Luiza Carvalhaes", "Marcelo Tavares", "Mariana Estrela", "Matheus Vilela",
    "Natália Bandeira", "Nelson Guimarães", "Otávio Rezende", "Patrícia Salgado",
    "Paulo Sérgio Bomfim", "Priscila Domingues", "Rafael Queiroz", "Raquel Amorim",
    "Renato Cavalcanti", "Roberta Mesquita", "Rodrigo Valadares", "Sabrina Portela",
    "Samuel Bragança", "Sandra Furtado", "Sérgio Mendonça", "Simone Nascimento",
    "Tatiane Lacerda", "Thiago Villaça", "Vanessa Coutinho", "Victor Hugo Paes",
    "Vinícius Toledo", "Wagner Bezerra", "Yasmin Delgado", "Zilda Monteiro",
]

COMPANIES = ["Techify", "Empresa Exemplo", "Loja do Zé", "Auto Peças Central",
             "Clínica Vida", "Mercado Bom Preço"]
PROFESSIONS = ["Analista", "Vendedor", "Gerente", "Autônomo", "Consultor", "Designer"]


def _email_for(name: str, idx: int) -> str:
    first = name.split()[0].lower()
    accents = str.maketrans("áàãâéêíóõôúüç", "aaaaeeiooouuc")
    return f"{first.translate(accents)}{idx:02d}@exemplo.test"


def _rows() -> list[tuple[str, str, str, str, str]]:
    """(phone, name, email, profession, company) de cada contato de teste."""
    out = []
    for i, name in enumerate(NAMES, start=1):
        phone = f"{PHONE_PREFIX}{i:02d}"
        out.append((phone, name, _email_for(name, i),
                    PROFESSIONS[i % len(PROFESSIONS)], COMPANIES[i % len(COMPANIES)]))
    return out


def _confirm(url: str, action: str, assume_yes: bool) -> bool:
    """Mostra o alvo e exige confirmação — o banco de dev fica no mesmo host de
    dados reais, então escrever nele nunca deve ser silencioso."""
    redacted = url
    if "@" in url:
        head, tail = url.split("@", 1)
        redacted = f"{head.split('://')[0]}://***@{tail}"
    print(f"Alvo: {redacted}")
    print(f"Ação: {action} {len(NAMES)} contatos de teste (prefixo {PHONE_PREFIX}*)")
    if assume_yes:
        return True
    return input("Confirma? [s/N] ").strip().lower() in ("s", "sim", "y", "yes")


def seed() -> None:
    from db.repositories import contact_repo, tag_repo

    if not tag_repo.get_by_name(TEST_TAG):
        tag_repo.create(TEST_TAG, TEST_TAG_COLOR)

    created = updated = 0
    for phone, name, email, profession, company in _rows():
        before = _existing_ids([phone])
        c = contact_repo.get_or_create(phone, contact_type="whatsapp")
        if before:
            updated += 1
        else:
            created += 1
        contact_repo.update(c["id"], name=name, email=email,
                            profession=profession, company=company)
        tag_repo.add_contact_tag(c["id"], TEST_TAG)
    print(f"OK — {created} criado(s), {updated} já existia(m). "
          f"Total de teste no banco: {len(_existing_ids())}")


def remove() -> None:
    from db.repositories import contact_repo

    ids = _existing_ids()
    for cid in ids:
        contact_repo.delete(cid)
    print(f"OK — {len(ids)} contato(s) de teste removido(s).")


def _existing_ids(phones: list[str] | None = None) -> list[int]:
    """Ids dos contatos na faixa de teste (opcionalmente restrito a `phones`)."""
    stmt = select(contacts.c.id).where(contacts.c.phone.like(f"{PHONE_PREFIX}%"))
    if phones is not None:
        stmt = stmt.where(contacts.c.phone.in_(phones))
    with get_engine().connect() as conn:
        return [r[0] for r in conn.execute(stmt)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--remove", action="store_true",
                    help="apaga os contatos de teste em vez de criá-los")
    ap.add_argument("--yes", action="store_true", help="não pedir confirmação")
    args = ap.parse_args()

    url = resolve_database_url()
    if not _confirm(url, "REMOVER" if args.remove else "CRIAR/ATUALIZAR", args.yes):
        print("Cancelado.")
        return 1
    init_engine(url)
    remove() if args.remove else seed()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
