"""Preferências de template do plugin whatsapp_cloud (plano 92 · D1).

Trava as duas semânticas que a tela depende e que são fáceis de inverter por
engano numa refatoração:

  * favorito é **por usuário** — o de um atendente não pode vazar para o outro;
  * arquivado é **global** — um marca e vale para todos, mas exige a permissão
    ``plugin.whatsapp_cloud.template_archive``, que nasce SEM DONO.

POR QUE ESTE TESTE MORA NO CORE, e não em ``<plugin>/tests/``: a fixture
``plugin_app`` vem de ``tests/conftest.py``, que o pytest só aplica à sua própria
árvore. Um teste dentro de ``storages/plugins/<id>/tests/`` é COLETADO (o
``pytest_configure`` o anexa aos roots) mas não enxerga a fixture — é o
bloqueador **P2 do plano 83**, ainda aberto. Quando ele cair, este arquivo pode
viajar junto com o zip do plugin.

Rodar::

    venv/bin/python -m pytest tests/test_whatsapp_cloud_template_prefs.py -q
"""

from __future__ import annotations

import pytest

CH = "wac_prefs_test"
BASE = "/api/plugins/whatsapp_cloud/template-prefs"


@pytest.fixture
def app(plugin_app):
    return plugin_app("whatsapp_cloud")


def _prefs(client, channel_id=CH):
    r = client.get(f"{BASE}?channel_id={channel_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    return body["data"]


# ── Leitura ────────────────────────────────────────────────────────────────

def test_prefs_vazio_no_comeco(app):
    data = _prefs(app.client)
    assert data["favorites"] == []
    assert data["archived"] == []


def test_prefs_exige_channel_id(app):
    r = app.client.get(BASE)
    assert r.json()["ok"] is False


# ── Favoritos ──────────────────────────────────────────────────────────────

def test_favoritar_exige_usuario_logado(app):
    """Instalação aberta não tem a quem pertencer o favorito.

    O painel esconde a estrela via ``can_favorite``; a rota recusa por garantia.
    """
    data = _prefs(app.client)
    assert data["can_favorite"] is False
    r = app.client.post(f"{BASE}/favorite",
                        json={"channel_id": CH, "template_name": "boas_vindas",
                              "favorite": True})
    assert r.json()["ok"] is False


def test_favoritar_e_desfavoritar_por_usuario(app):
    """Escreve direto na tabela (a rota exige sessão) e lê pela rota."""
    from sqlalchemy import text
    from plugins.context import make_plugin_db

    with make_plugin_db() as conn:
        for uid, nome in ((901, "so_do_901"), (902, "so_do_902")):
            conn.execute(text(
                "INSERT INTO plugin_whatsapp_cloud_template_favorites "
                "(user_id, channel_id, template_name, created_at) "
                "VALUES (:u, :c, :n, 0)"), {"u": uid, "c": CH, "n": nome})

        do901 = [r[0] for r in conn.execute(text(
            "SELECT template_name FROM plugin_whatsapp_cloud_template_favorites "
            "WHERE user_id = 901 AND channel_id = :c"), {"c": CH})]
        do902 = [r[0] for r in conn.execute(text(
            "SELECT template_name FROM plugin_whatsapp_cloud_template_favorites "
            "WHERE user_id = 902 AND channel_id = :c"), {"c": CH})]

    # O ponto do teste: favorito NÃO vaza entre atendentes.
    assert do901 == ["so_do_901"]
    assert do902 == ["so_do_902"]

    # E sem usuário na request, a leitura não devolve favorito de ninguém.
    assert _prefs(app.client)["favorites"] == []


def test_indice_unico_impede_favorito_duplicado(app):
    from sqlalchemy import text
    from plugins.context import make_plugin_db
    from sqlalchemy.exc import IntegrityError

    with make_plugin_db() as conn:
        conn.execute(text(
            "INSERT INTO plugin_whatsapp_cloud_template_favorites "
            "(user_id, channel_id, template_name, created_at) "
            "VALUES (903, :c, 'dup', 0)"), {"c": CH})
    with pytest.raises(IntegrityError):
        with make_plugin_db() as conn:
            conn.execute(text(
                "INSERT INTO plugin_whatsapp_cloud_template_favorites "
                "(user_id, channel_id, template_name, created_at) "
                "VALUES (903, :c, 'dup', 0)"), {"c": CH})


# ── Arquivados ─────────────────────────────────────────────────────────────

def test_arquivar_e_global_e_reflete_para_todos(app):
    """Sem escopo de usuário: quem ler o canal vê o mesmo arquivado."""
    r = app.client.post(f"{BASE}/archive",
                        json={"channel_id": CH, "template_name": "morto_1",
                              "archived": True})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    data = _prefs(app.client)
    assert "morto_1" in data["archived"]

    # Desarquivar devolve à lista.
    app.client.post(f"{BASE}/archive",
                    json={"channel_id": CH, "template_name": "morto_1",
                          "archived": False})
    assert "morto_1" not in _prefs(app.client)["archived"]


def test_arquivar_e_idempotente(app):
    for _ in range(3):
        r = app.client.post(f"{BASE}/archive",
                            json={"channel_id": CH, "template_name": "idem",
                                  "archived": True})
        assert r.json()["ok"] is True
    assert _prefs(app.client)["archived"].count("idem") == 1


def test_arquivado_e_por_canal(app):
    """O mesmo nome em WABAs diferentes é outro template."""
    app.client.post(f"{BASE}/archive",
                    json={"channel_id": "wac_canal_a", "template_name": "x",
                          "archived": True})
    assert "x" in _prefs(app.client, "wac_canal_a")["archived"]
    assert "x" not in _prefs(app.client, "wac_canal_b")["archived"]


def test_arquivar_exige_nome_e_canal(app):
    assert app.client.post(f"{BASE}/archive", json={"channel_id": CH}).json()["ok"] is False
    assert app.client.post(f"{BASE}/archive", json={"template_name": "y"}).json()["ok"] is False


def test_permissao_declarada_no_manifest(app):
    """A chave existe no catálogo (o admin a concede em Usuários → Cargos).

    Nasce sem dono por decisão de produto — nenhuma migração concede.
    """
    from db.repositories import rbac_repo
    chaves = {p["key"] for p in rbac_repo.list_catalog()}
    assert "plugin.whatsapp_cloud.template_archive" in chaves
