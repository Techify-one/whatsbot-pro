"""Casamento do clique em botão → AÇÃO.

Módulo **puro**: sem banco, sem rede, sem import do core. Tudo aqui é função de
entrada/saída, e é de propósito — é a parte da feature que precisa ser
exercitada em milissegundos, sem subir app nem Postgres.

Quatro responsabilidades:

1. **Extrair o token** do ``media_extras`` que o canal WhatsApp Cloud produz.
2. **Normalizar** esse token e os códigos configurados, para casarem.
3. **Indexar** os códigos de cada ação e casar um clique com uma delas
   (:func:`parse_codes`, :func:`code_index`, :func:`action_for`).
4. **Traduzir o contrato do Campanhas** (``frees_contact``), que é a única coisa
   que liga os dois sistemas depois desta mudança.

Este módulo NÃO sabe o que uma ação faz — ele devolve um id opaco. Quem declara
o catálogo e o que cada ação grava é ``actions.py``; quem entrega é ``consent``.

Por que o casamento é pelo PAYLOAD e não por "nome do template + índice do
botão": o objeto que a Meta manda no clique **não carrega nome de template nem
índice**. O único elo seria ``context.id`` (o wamid da mensagem original), que
exigiria ter registrado o envio — e quem envia o template é o módulo Campanhas,
fora deste WhatsBot.

O payload, por sua vez, é **escolhido pelo Campanhas** no momento do disparo
(componente ``button`` / ``sub_type: quick_reply`` / ``type: payload``). É por
isso que existem códigos configuráveis em vez de uma tabela de regras: o
operador não precisa adivinhar qual string a Meta devolve, porque é ele quem a
define nos dois lados.
"""

from __future__ import annotations

import re
import unicodedata

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
        # Quick-reply de template. ⚠️ Quando o template NÃO define payload
        # explícito, a Meta ecoa o TEXTO do botão aqui. Isso deixou de ser um
        # problema (o Campanhas passou a mandar o payload sempre) e virou uma
        # garantia: um template antigo, sem payload, ecoa o rótulo e portanto
        # nunca casa o código — não descadastra ninguém por engano.
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


# ── Códigos → ação ───────────────────────────────────────────────────────

def parse_codes(raw) -> list[str]:
    """``"A, B , , a "`` → ``["A", "B"]``.

    Devolve os códigos **como digitados** (a aba os ecoa de volta ao operador),
    mas descarta o segundo de um par que normaliza igual: ``"PARAR, parar"`` é
    um código só, e listar os dois faria a tela mentir sobre quantos existem.

    Vírgula é o separador porque é o que cabe numa setting escalar — o PUT de
    settings do core é destrutivo e reescreve o objeto inteiro, então uma lista
    de verdade em settings seria revertida por qualquer save de outra aba.
    """
    vistos: set[str] = set()
    saida: list[str] = []
    for parte in str(raw or "").split(","):
        codigo = _text(parte)
        if not codigo:
            continue
        chave = normalize(codigo)
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        saida.append(codigo)
    return saida


def code_index(codes_by_action: dict) -> tuple[dict, dict]:
    """``{action_id: [códigos]}`` → ``({código_norm: action_id}, conflitos)``.

    Conflito = o mesmo código reivindicado por DUAS ações. Ele não fica com
    nenhuma: sai do índice e entra em ``{código_norm: [action_id, ...]}``, que a
    tela mostra em vermelho.

    "A primeira ganha" faria o comportamento depender da ordem de declaração num
    arquivo ``.py`` — invisível ao operador — e o lado errado do erro escreve no
    CRM do cliente. Recusar os dois só apaga AQUELE código: os demais códigos
    das duas ações continuam valendo.
    """
    index: dict[str, str] = {}
    donos: dict[str, list[str]] = {}
    for action_id, codigos in (codes_by_action or {}).items():
        for codigo in codigos or []:
            chave = normalize(codigo)
            if not chave:
                continue
            if action_id not in donos.setdefault(chave, []):
                donos[chave].append(action_id)

    conflitos = {}
    for chave, ids in donos.items():
        if len(ids) > 1:
            conflitos[chave] = ids
        else:
            index[chave] = ids[0]
    return index, conflitos


def action_for(cand: dict, index: dict) -> str | None:
    """Este clique é qual ação? ``None`` = nenhuma (código desconhecido).

    **Só o payload conta.** O rótulo do botão fica de fora de propósito: o
    payload é escolhido pelo Campanhas e é um código, enquanto o rótulo é texto
    de marketing que muda a cada campanha e pode coincidir por acaso com o de
    outro botão. Aceitar o rótulo reintroduziria exatamente o falso positivo que
    o código explícito veio eliminar.

    A comparação é normalizada (acento/caixa/espaço) porque o mesmo código é
    digitado à mão nos dois sistemas. Payload vazio nunca casa, nem com o índice
    cheio — é a guarda contra "configuração pela metade descadastra todo mundo".
    """
    if not cand or not index:
        return None
    chave = cand.get("payload_norm") or ""
    if not chave:
        return None
    return index.get(chave)


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


def validate_values(optout_value: str) -> list[str]:
    """Erros de configuração do valor gravado no campo. Lista vazia = ok.

    Existe porque um operador que escolhe ``0`` como "descadastrado" produz uma
    configuração que **parece** funcionar (o valor é gravado, nenhum erro
    aparece) e não bloqueia ninguém. É a única checagem que traduz o contrato do
    Campanhas para a tela.
    """
    erros: list[str] = []
    if not blocks_contact(optout_value):
        erros.append(
            f"O valor de descadastro ({optout_value!r}) NÃO bloqueia o contato: "
            "o Campanhas trata vazio, 'nao', 'não', 'n', 'no', 'false' e '0' "
            "como quem continua recebendo. Use 'sim', 'true' ou a data do "
            "descadastro.")
    return erros
