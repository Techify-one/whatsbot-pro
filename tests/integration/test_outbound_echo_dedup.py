"""O echo de uma mensagem que o PRÓPRIO app enviou não pode virar uma 2ª bolha.

Messenger/Instagram reentregam toda mensagem da Página como ``message_echoes`` —
inclusive as que saíram pela Send API —, e o ``mid`` do echo é o mesmo id que o
envio devolveu. O core tem uma trava para isso (``processed_messages``), mas os
dois lados montavam a chave de formas diferentes: o envio gravava o id CRU e o
funil de ingestão procurava ``"<channel_id>:<id>"``. Nunca se encontravam.

O texto escondia a falha atrás da trava heurística ``recently_sent`` (chaveada
pelo texto do fio); a mídia não tinha trava nenhuma — ``recently_sent`` nunca é
escrito no caminho de mídia. Uma imagem enviada pelo painel aparecia DUAS vezes:
a cópia real (com ``sent_by_name``, rotulada "Automação"/"IA") e o echo salvo
como operador anônimo (rotulado "Manual").

⚠️ Este arquivo cobre só a parte que é do CORE: a chave. Quem de fato cala o
echo dos canais Meta é o PROVIDER — ``meta_graph`` lembra os mids que ele mesmo
enviou e nem emite o evento (é o único que sabe do 2º envio que uma legenda
gera, cujo id nunca chega ao core porque ``SendResult`` carrega um id só). Os
testes disso vivem no repositório dos plugins.

O canal do teste é o GOWA porque ele é o único provider bundled e a costura
consertada é a genérica (``_ingest_echo``, comum a todos os providers): o
webhook com ``is_from_me`` produz exatamente o mesmo ``direction="out"`` que o
``is_echo`` da Meta.

    venv/bin/python -m pytest tests/integration/test_outbound_echo_dedup.py -q
"""

from __future__ import annotations

import io

from db.repositories import contact_repo, message_repo


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def _echo_payload(phone: str, msg_id: str, **extra) -> dict:
    payload = {"from": f"{phone}@s.whatsapp.net", "id": msg_id,
               "is_from_me": True, "from_name": "Eu"}
    payload.update(extra)
    return {"event": "message", "payload": payload}


def _assistant_rows(phone: str) -> list[dict]:
    contact = contact_repo.get_by_phone(phone)
    assert contact is not None, f"contato {phone} não materializado"
    return [m for m in message_repo.get_all(contact["id"])
            if m["role"] == "assistant"]


def _send_image(built, phone: str) -> str:
    r = built.client.post(
        f"/api/contacts/{phone}/send-image",
        files={"image": ("foto.png", io.BytesIO(PNG), "image/png")})
    assert r.status_code == 200, r.text
    msg_id = (r.json().get("data") or {}).get("msg_id")
    assert msg_id, "o envio precisa devolver o id externo do canal"
    return msg_id


def test_media_send_registers_the_canonical_echo_key(build_app):
    """O produtor grava EXATAMENTE a chave que o consumidor procura.

    É a asserção que trava o defeito na raiz: a chave leva o prefixo do canal,
    que é o formato que ``_ingest_echo`` procura. O id CRU — o que o envio de
    mídia gravava antes — não pode voltar a aparecer sozinho no conjunto.
    """
    phone = "5511970009001"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    msg_id = _send_image(built, phone)

    processed = built.app.state.deps.state.processed_messages
    assert f"default:{msg_id}" in processed
    assert msg_id not in processed, "chave crua = o formato que nunca casava"


def test_media_echo_of_our_own_send_is_not_saved_twice(build_app):
    """O bug do relato: imagem enviada pelo painel duplicava no fio.

    Sem legenda, então o guard de texto (``recently_sent``) não teria como
    ajudar nem se estivesse populado — a supressão depende só do id.
    """
    phone = "5511970009002"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    msg_id = _send_image(built, phone)
    assert len(_assistant_rows(phone)) == 1

    r = built.client.post(
        "/api/webhook/gowa/default",
        json=_echo_payload(phone, msg_id, image={"path": "statics/media/foto.png"}))
    assert r.status_code == 200, r.text

    rows = _assistant_rows(phone)
    assert len(rows) == 1, f"echo do próprio envio duplicou a mídia: {rows}"


def test_media_echo_with_caption_is_not_saved_twice(build_app):
    """Legenda não muda nada: ``recently_sent`` nunca é escrito no envio de mídia.

    Antes do conserto este caso duplicava mesmo COM texto no echo — o guard
    heurístico só cobre os caminhos de texto puro.
    """
    phone = "5511970009003"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    r = built.client.post(
        f"/api/contacts/{phone}/send-image",
        files={"image": ("foto.png", io.BytesIO(PNG), "image/png")},
        data={"caption": "olha a foto"})
    assert r.status_code == 200, r.text
    msg_id = (r.json().get("data") or {}).get("msg_id")

    r = built.client.post("/api/webhook/gowa/default", json=_echo_payload(
        phone, msg_id, image={"path": "statics/media/foto.png",
                              "caption": "olha a foto"}))
    assert r.status_code == 200, r.text
    assert len(_assistant_rows(phone)) == 1


def test_echo_from_the_users_own_phone_is_still_ingested(build_app):
    """A trava não pode engolir o que ela existe para sincronizar.

    Um id que o app nunca enviou é uma mensagem escrita no celular do usuário —
    tem de continuar entrando no fio como ``assistant``/operador.
    """
    phone = "5511970009004"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    r = built.client.post("/api/webhook/gowa/default", json=_echo_payload(
        phone, "echo_do_celular_1", body="mandei isso do celular"))
    assert r.status_code == 200, r.text

    rows = _assistant_rows(phone)
    assert len(rows) == 1
    assert rows[0]["content"] == "mandei isso do celular"


def test_provider_redelivery_of_a_foreign_echo_is_still_deduped(build_app):
    """A idempotência que já existia (P18) continua valendo para o echo alheio."""
    phone = "5511970009005"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    for _ in range(2):
        r = built.client.post("/api/webhook/gowa/default", json=_echo_payload(
            phone, "echo_do_celular_2", body="reentregue duas vezes"))
        assert r.status_code == 200, r.text

    assert len(_assistant_rows(phone)) == 1
