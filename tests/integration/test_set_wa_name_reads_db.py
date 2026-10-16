"""``ContactMemory.set_wa_name`` decide pelo nome do BANCO, não pelo cache.

``ContactMemory`` é cacheado por ``(channel_id, phone)``. O nome pode mudar por
fora do objeto (edição no painel por outra instância, o plugin ``trackify``
trazendo o nome do CRM). Confiar no cache deixava um ``~Fulano`` velho
sobrescrever um nome real na mensagem seguinte.

    venv/bin/python -m pytest tests/integration/test_set_wa_name_reads_db.py -q
"""

from __future__ import annotations

from agent.memory import ContactMemory
from db.repositories import contact_repo

PHONE = "5511666660777"

def _name() -> str:
    return contact_repo.get_by_phone(PHONE)["name"]

def test_nome_real_gravado_por_fora_nao_e_sobrescrito_pelo_perfil(_engine_ready):
    c = contact_repo.get_or_create(PHONE)
    contact_repo.update(c["id"], name="~Ana Perfil")
    cached = ContactMemory(PHONE, channel_id="canal-x")      # cache com '~Ana Perfil'
    assert cached.info["name"] == "~Ana Perfil"

    contact_repo.update(c["id"], name="Ana do CRM")          # mudou por fora do cache
    cached.set_wa_name("Ana Perfil Novo")

    assert _name() == "Ana do CRM"
    assert cached.info["name"] == "Ana do CRM"               # cache ressincronizado

def test_save_do_cache_nao_desfaz_o_nome_real(_engine_ready):
    c = contact_repo.get_or_create(PHONE)
    contact_repo.update(c["id"], name="~Ana Perfil")
    cached = ContactMemory(PHONE, channel_id="canal-x")
    contact_repo.update(c["id"], name="Ana do CRM")

    cached.set_wa_name("Qualquer")      # ressincroniza o cache com o banco
    cached.save()                       # o save() regrava o nome do cache

    assert _name() == "Ana do CRM"

def test_nome_vazio_e_nome_de_perfil_continuam_recebendo_o_perfil(_engine_ready):
    c = contact_repo.get_or_create(PHONE)
    contact_repo.update(c["id"], name="")
    cached = ContactMemory(PHONE, channel_id="canal-y")

    cached.set_wa_name("Ana A")
    assert _name() == "~Ana A"
    cached.set_wa_name("Ana B")
    assert _name() == "~Ana B"
