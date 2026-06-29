"""Manual QA harness for the WhatsApp Cloud API channel (caixa API oficial).

NOT part of the automated suite — run by hand against a real WABA.
Credentials are read from the environment (never hard-coded / committed):

    META_ACCESS_TOKEN, PHONE_NUMBER_ID, META_API_VERSION,
    WABA_ID (optional — auto-discovered if absent), CLOUD_TEST_TO (destino)

It exercises the REAL production class ``WhatsAppCloudChannel`` for
status/send_text/send_media/send_template, plus a few direct Graph calls for
WABA discovery / template listing / template creation (which the channel does
not implement). Each step prints a PASS/FAIL/INFO line; nothing aborts the run.
"""

from __future__ import annotations

import datetime
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assets.plugin_examples.whatsapp_cloud.channels import WhatsAppCloudChannel  # noqa: E402

TOKEN = os.environ["META_ACCESS_TOKEN"]
PHONE_ID = os.environ["PHONE_NUMBER_ID"]
VERSION = os.environ.get("META_API_VERSION", "v21.0")
WABA_ID = os.environ.get("WABA_ID", "")
TO = os.environ.get("CLOUD_TEST_TO", "")

GRAPH = f"https://graph.facebook.com/{VERSION}"
HEAD = {"Authorization": f"Bearer {TOKEN}"}


def line(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}", flush=True)


# Build the production channel with in-memory creds (registry=None path).
ch = WhatsAppCloudChannel(
    channel_id="qa_cloud",
    registry=None,
    credentials={
        "access_token": TOKEN,
        "phone_number_id": PHONE_ID,
        "graph_api_version": VERSION,
    },
)

# ── 1. STATUS (valida token + phone_number_id) ────────────────────────────
section("1. status() — valida token + phone_number_id")
st = ch.status()
line("STATUS", str(st))
if st.get("connected"):
    line("PASS", f"Token+phone_id válidos. número={st.get('display_phone_number')} "
                 f"nome={st.get('verified_name')} qualidade={st.get('quality_rating')}")
else:
    line("FAIL", f"status() não conectou: {st.get('error')}")

# ── 2. Descoberta do WABA id (necessário p/ templates) ────────────────────
section("2. Descobrir WABA id (GET /{phone_id}?fields=...)")
waba_id = WABA_ID
if not waba_id:
    try:
        r = httpx.get(f"{GRAPH}/{PHONE_ID}",
                      params={"fields": "whatsapp_business_account{id,name},display_phone_number,verified_name"},
                      headers=HEAD, timeout=20)
        line("HTTP", f"{r.status_code} {r.text[:300]}")
        if r.status_code == 200:
            waba = (r.json().get("whatsapp_business_account") or {})
            waba_id = waba.get("id", "")
            if waba_id:
                line("PASS", f"WABA id descoberto: {waba_id} ({waba.get('name')})")
            else:
                line("INFO", "Resposta 200 mas sem whatsapp_business_account no fields.")
        else:
            line("FAIL", "Não foi possível descobrir o WABA id (necessário p/ templates).")
    except Exception as e:  # noqa: BLE001
        line("FAIL", f"discovery erro: {e}")
else:
    line("INFO", f"WABA id veio do ambiente: {waba_id}")

# ── 3. Listar templates aprovados (GET /{waba}/message_templates) ─────────
section("3. Listar templates do WABA (item: base p/ envio de template)")
templates = []
if waba_id:
    try:
        r = httpx.get(f"{GRAPH}/{waba_id}/message_templates",
                      params={"limit": 50}, headers=HEAD, timeout=20)
        line("HTTP", f"{r.status_code}")
        if r.status_code == 200:
            templates = r.json().get("data", [])
            line("PASS", f"{len(templates)} template(s) encontrados:")
            for t in templates:
                line("  TPL", f"{t.get('name')} | lang={t.get('language')} | "
                              f"status={t.get('status')} | cat={t.get('category')}")
        else:
            line("FAIL", f"listagem falhou: {r.text[:300]}")
    except Exception as e:  # noqa: BLE001
        line("FAIL", f"listagem erro: {e}")
else:
    line("SKIP", "sem WABA id — não dá pra listar templates")

# ── 4. Envio de TEMPLATE (send_template do canal real) ────────────────────
section("4. send_template() — envio de template (fora da janela 24h é o único permitido)")
if TO:
    # Prefer an APPROVED template from the WABA; fall back to hello_world.
    approved = [t for t in templates if (t.get("status") == "APPROVED")]
    if approved:
        t = approved[0]
        tpl_name, tpl_lang = t.get("name"), t.get("language")
    else:
        tpl_name, tpl_lang = "hello_world", "en_US"
    line("INFO", f"usando template '{tpl_name}' ({tpl_lang}) → {TO}")
    res = ch.send_template(TO, tpl_name, lang=tpl_lang)
    line("RESULT", f"ok={res.ok} id={res.external_msg_id} err={res.error}")
    line("PASS" if res.ok else "FAIL", "envio de template")
else:
    line("SKIP", "sem CLOUD_TEST_TO")

# ── 5. Envio de TEXTO livre (testa a regra das 24h do lado da Meta) ───────
section("5. send_text() — texto livre (revela bloqueio de 24h da Meta)")
if TO:
    res = ch.send_text(TO, "Teste WhatsBot QA — texto livre (janela de sessão).")
    line("RESULT", f"ok={res.ok} id={res.external_msg_id} err={res.error}")
    if res.ok:
        line("PASS", "texto livre aceito → janela de 24h ABERTA (houve inbound recente)")
    elif res.error and ("131047" in res.error or "24 hours" in res.error or "re-engage" in res.error):
        line("PASS", "Meta BLOQUEOU texto livre fora da janela de 24h (erro 131047) — "
                     "comportamento esperado. WhatsBot NÃO bloqueia localmente.")
    else:
        line("INFO", f"falhou por outro motivo: {res.error}")
else:
    line("SKIP", "sem CLOUD_TEST_TO")

# ── 6. Envio de MÍDIA ─────────────────────────────────────────────────────
section("6. send_media() — testa o caminho real + o bug do path local")
if TO:
    # 6a. URL pública (caminho que a API aceita) — prova que send_media funciona.
    pub_url = "https://www.gstatic.com/webp/gallery/1.jpg"
    res = ch.send_media(TO, "image", pub_url, caption="QA: imagem por URL pública")
    line("6a URL", f"ok={res.ok} id={res.external_msg_id} err={res.error}")
    line("PASS" if res.ok else "FAIL", "send_media com URL pública (image)")

    # 6b. Caminho LOCAL de disco — é o que as rotas do painel passam hoje.
    local_path = "/tmp/whatsbot_qa_nonexistent.jpg"
    res2 = ch.send_media(TO, "image", local_path, caption="QA: path local")
    line("6b LOCAL", f"ok={res2.ok} err={res2.error}")
    if not res2.ok:
        line("PASS", "Confirmado BUG: path local não é aceito pela Meta (precisa "
                     "URL pública ou upload /media → media_id). Rotas do painel "
                     "passam str(dest) local → mídia falha no canal Cloud.")
    else:
        line("INFO", "path local aceito (inesperado).")
else:
    line("SKIP", "sem CLOUD_TEST_TO")

# ── 7. CRIAÇÃO de template (POST /{waba}/message_templates) ───────────────
section("7. Criação de template (feature AUSENTE no código — testando via Graph direto)")
if os.environ.get("CREATE_TEMPLATE") == "1" and waba_id:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    name = f"whatsbot_qa_test_{stamp}"
    body = {
        "name": name,
        "language": "pt_BR",
        "category": "UTILITY",
        "components": [
            {"type": "BODY", "text": "Olá! Este é um template de teste do WhatsBot QA."}
        ],
    }
    try:
        r = httpx.post(f"{GRAPH}/{waba_id}/message_templates", headers=HEAD, json=body, timeout=20)
        line("HTTP", f"{r.status_code} {r.text[:300]}")
        if r.status_code in (200, 201):
            line("PASS", f"template '{name}' criado (ficará PENDING até aprovação Meta).")
        else:
            line("FAIL", "criação de template falhou.")
    except Exception as e:  # noqa: BLE001
        line("FAIL", f"criação erro: {e}")
else:
    line("SKIP", "criação de template desativada (set CREATE_TEMPLATE=1 + WABA id p/ rodar). "
                 "Lembre: NÃO existe código no WhatsBot para isso — só Graph direto.")

print("\n--- fim do harness ---", flush=True)
