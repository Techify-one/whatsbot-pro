"""Casamento do clique em botão → significado de consentimento.

Módulo **puro**: sem banco, sem rede, sem import do core. Tudo aqui é função de
entrada/saída, e é de propósito — é a parte da feature que precisa ser
exercitada em milissegundos, sem subir app nem Postgres.

Três responsabilidades:

1. **Extrair o token** do ``media_extras`` que o canal WhatsApp Cloud produz.
2. **Normalizar** esse token e o que o operador digitou, para os dois casarem.
3. **Traduzir o contrato do Campanhas** (``frees_contact``), que é a única coisa
   que liga os dois sistemas depois desta mudança.

Por que a chave de casamento é o TOKEN e não "nome do template + índice do
botão": o objeto que a Meta manda no clique **não carrega nome de template nem
índice**. O único elo seria ``context.id`` (o wamid da mensagem original), que
exigiria ter registrado o envio — e quem envia o template é o módulo Campanhas,
fora deste WhatsBot. O texto/payload do botão é o único dado presente em todas
as formas e independente de quem disparou.
"""

from __future__ import annotations

import re
import unicodedata

# ── Significados ─────────────────────────────────────────────────────────

OPTOUT = "optout"    # não quer mais receber
OPTIN = "optin"      # quer continuar recebendo
IGNORE = "ignore"    # botão conhecido e deliberadamente sem efeito

MEANINGS = (OPTOUT, OPTIN, IGNORE)

# ── Formas que a Meta usa para um clique de botão ────────────────────────
#
# Duas famílias, e as duas chegam no MESMO campo (``media_extras["payload"]``):
#
#   type "button"      → {"payload": "<string>", "text": "<rótulo>"}
#                        (quick-reply de TEMPLATE — o caso desta feature)
#   type "interactive" → {"type": "button_reply", "button_reply": {"id","title"}}
#                        (também "list_reply", para menu de lista)
#
# ``nfm_reply`` (resposta de Flow) fica de fora: é um formulário, não um botão
# de consentimento, e tratá-lo como tal produziria descadastro por engano.
SHAPE_BUTTON = "button"
SHAPE_BUTTON_REPLY = "button_reply"
SHAPE_LIST_REPLY = "list_reply"

_INTERACTIVE_SUBTYPES = (SHAPE_BUTTON_REPLY, SHAPE_LIST_REPLY)

_WS = re.compile(r"\s+")


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def normalize(value) -> str:
    """Forma canônica usada para casar regra e clique.

    ``strip`` → NFKD sem marcas de combinação (tira acento) → ``casefold`` →
    colapso de espaço interno.

    Acento-insensível porque o operador digita a regra à mão e o rótulo do botão
    vem da Meta: "NÃO QUERO RECEBER" tem de casar "nao quero receber".

    **Emoji e pontuação são preservados** de propósito. Removê-los faria
    "🚫 Parar" colidir com "Parar", que podem ser botões diferentes do mesmo
    template — e a tela de "botões vistos" já entrega a string exata, então ser
    literal não custa nada ao operador.
    """
    s = _text(value)
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return _WS.sub(" ", s).casefold().strip()


def tokens(media_extras) -> dict | None:
    """``media_extras`` do core → ``{payload, text, shape, *_norm}`` ou ``None``.

    ``None`` significa "isto não é um clique de botão que eu saiba interpretar",
    e é a **segunda linha de defesa** depois do gate de provider: o GOWA entrega
    ``{"button_id", "title"}``, forma que não casa este contrato e portanto nunca
    produz consentimento mesmo se o gate de canal falhasse.
    """
    if not isinstance(media_extras, dict):
        return None
    inter = media_extras.get("payload")
    if not isinstance(inter, dict):
        return None

    subtype = inter.get("type")
    if isinstance(subtype, str) and subtype in _INTERACTIVE_SUBTYPES:
        reply = inter.get(subtype)
        if not isinstance(reply, dict):
            return None
        payload, label, shape = _text(reply.get("id")), _text(reply.get("title")), subtype
    elif "payload" in inter:
        # Quick-reply de template. ⚠️ Quando o template não define payload
        # explícito, a Meta ecoa o TEXTO do botão aqui — por isso o casamento
        # aceita os dois campos em vez de exigir um payload estável.
        payload, label, shape = _text(inter.get("payload")), _text(inter.get("text")), SHAPE_BUTTON
    else:
        return None

    if not (payload or label):
        return None
    return {
        "payload": payload,
        "text": label,
        "shape": shape,
        "payload_norm": normalize(payload),
        "text_norm": normalize(label),
    }


def seen_key(cand: dict) -> str:
    """Token pelo qual um clique é registrado em "botões vistos".

    O payload manda quando existe — é o mais estável dos dois.
    """
    return (cand or {}).get("payload_norm") or (cand or {}).get("text_norm") or ""


def resolve(cand: dict, rules, channel_id: str = "") -> dict | None:
    """Regra que casa o clique, ou ``None``.

    Precedência, nesta ordem: **regra do canal antes da global**, e dentro de
    cada escopo **payload antes do rótulo**. Determinística de propósito: com
    dois botões cujo rótulo coincide, o operador precisa conseguir prever qual
    regra vence sem ler o código.
    """
    if not cand:
        return None

    index: dict[tuple[str, str], dict] = {}
    for r in rules or []:
        try:
            if not int(r.get("enabled", 1) or 0):
                continue
        except (TypeError, ValueError):
            continue
        norm = str(r.get("match_norm") or "")
        if not norm:
            continue
        # O UNIQUE (channel_id, match_norm) garante no máximo uma linha por
        # chave, então o primeiro a chegar é o único.
        index.setdefault((str(r.get("channel_id") or ""), norm), r)

    scopes: list[str] = []
    for s in (str(channel_id or ""), ""):
        if s not in scopes:
            scopes.append(s)

    for scope in scopes:
        for kind in ("payload", "text"):
            tok = cand.get(f"{kind}_norm") or ""
            if not tok:
                continue
            r = index.get((scope, tok))
            if not r:
                continue
            mf = str(r.get("match_field") or "any")
            if mf not in ("any", kind):
                continue
            return r
    return None


# ── O contrato do campo de descadastro (lado Campanhas) ──────────────────
#
# Espelha `VALORES_LIBERAM` de
# `server/modules/campanhas/trackify-contacts.service.ts`, onde o gate é
# aplicado em SQL como:
#
#     <campo>.value IS NULL OR lower(btrim(<campo>.value)) IN (...)
#
# `btrim` sem argumento remove ESPAÇOS, não todo espaço em branco — por isso o
# strip abaixo é `" "` e não o default do Python. Divergir aqui na direção
# errada seria o pior bug possível da feature: gravaríamos um valor achando que
# bloqueia, e o Campanhas continuaria disparando para quem pediu para sair.
FREEING_VALUES = ("", "nao", "não", "n", "no", "false", "0")


def frees_contact(value) -> bool:
    """O contato ENTRA na lista de disparo com este valor no campo?

    ``None`` (campo sem linha no CDP) libera. Qualquer valor fora da lista
    bloqueia — inclusive uma data ISO, que é uma das grafias previstas no
    contrato.
    """
    if value is None:
        return True
    return str(value).strip(" ").lower() in FREEING_VALUES


def blocks_contact(value) -> bool:
    """Inverso de :func:`frees_contact`. É o que um valor de opt-out precisa ser."""
    return not frees_contact(value)


def validate_values(optout_value: str, optin_value: str) -> list[str]:
    """Erros de configuração dos valores gravados no campo. Lista vazia = ok.

    Existe porque um operador que escolhe ``0`` como "descadastrado" produz uma
    configuração que **parece** funcionar (o valor é gravado, a fila fica verde)
    e não bloqueia ninguém. É a única checagem que traduz o contrato do
    Campanhas para a tela.
    """
    erros: list[str] = []
    if not blocks_contact(optout_value):
        erros.append(
            f"O valor de descadastro ({optout_value!r}) NÃO bloqueia o contato: "
            "o Campanhas trata vazio, 'nao', 'não', 'n', 'no', 'false' e '0' "
            "como quem continua recebendo. Use 'sim', 'true' ou a data do "
            "descadastro.")
    if not frees_contact(optin_value):
        erros.append(
            f"O valor de reinscrição ({optin_value!r}) mantém o contato "
            "bloqueado. Use vazio (apaga o campo), 'nao' ou 'false'.")
    return erros
