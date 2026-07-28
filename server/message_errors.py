"""Catálogo de códigos de falha de envio → frase PT-BR acionável (plano 75 F6).

Quando o provedor aceita o POST e só depois avisa, pelo webhook, que a mensagem
NÃO foi entregue (``statuses[].status == "failed"``), o operador precisa saber
**por quê** sem abrir log nenhum. Este módulo traduz o código numérico do erro na
frase que vai para o card ``role="error"`` no fio da conversa.

Onde isto mora (pergunta P3 do plano, decidida na F6): **no core**. É um
dicionário inerte — vocabulário do protocolo, não comportamento de provider.
Não há nenhum ``if provider ==`` aqui: quem chama passa código + textos já
extraídos do payload, e o módulo só formata. Quando um 2º provider trouxer um
espaço de códigos próprio, isto vira um gancho ``describe_status_error()`` no
contrato ``Channel`` — até lá, um dict no core é o desenho mais simples que
funciona.

Fonte dos códigos: doc oficial da Meta (plano 75 §12.9). ⚠️ **470** e **63016**
NÃO existem na Cloud API (são On-Premises legado e Twilio, respectivamente) —
não adicione (falso positivo #6 do plano). O equivalente é **131047**.

Regra de ouro: **nunca devolver string vazia**. Código desconhecido cai no
``title``/``details`` que a própria Meta manda (inglês legível) e, se nem isso
vier, no próprio número do código.
"""

from __future__ import annotations

# Código do provedor → frase PT-BR acionável (o que o operador deve fazer).
# O texto NÃO repete "Erro no envio": o card já tem esse cabeçalho
# (SystemMessageCard.js, role="error").
ERROR_HINTS: dict[int, str] = {
    131047: ("Não entregue: passaram mais de 24h desde a última resposta do "
             "cliente. Use um template."),
    131049: ("Não entregue: o WhatsApp limitou mensagens de marketing para este "
             "cliente. Aguarde antes de reenviar."),
    131026: ("Não entregue: o número não é WhatsApp, não aceitou os termos de "
             "uso ou está com o aplicativo desatualizado."),
    132001: "Template não existe nesse idioma ou não foi aprovado.",
    132000: "Template com número de parâmetros diferente do cadastrado.",
    132015: "Template pausado por baixa qualidade.",
    132016: "Template desabilitado permanentemente por baixa qualidade.",
    131053: "Falha ao subir a mídia da mensagem.",
    130472: "Não enviada: experimento de entrega do WhatsApp (grupo de controle).",
    131051: "Tipo de mensagem não suportado.",
    131000: "Erro desconhecido no envio.",
    130429: ("Limite de vazão da Cloud API atingido; tente novamente em "
             "instantes."),
    131056: "Mensagens demais para o mesmo destinatário em pouco tempo.",
}

# Texto de último recurso: o provedor disse "falhou" e não mandou código nem
# descrição alguma (não deveria acontecer, mas um card vazio é pior).
UNKNOWN_REASON = "Falha no envio: o provedor não informou o motivo."


def _coerce_code(code) -> int | None:
    """``code`` como int quando der; ``None`` quando não for numérico."""
    if isinstance(code, bool):  # bool é subclasse de int — nunca é um código
        return None
    if isinstance(code, int):
        return code
    if isinstance(code, str):
        try:
            return int(code.strip())
        except ValueError:
            return None
    return None


def describe_failure(code=None, title: str = "", details: str = "") -> str:
    """Frase PT-BR do card de erro para uma falha de envio.

    ``code`` é o código do provedor (int, string numérica ou ``None``);
    ``title`` e ``details`` são os textos que a Meta manda no item de
    ``errors[]`` (``title``/``message`` e ``error_data.details``) — em inglês,
    mas legíveis, e por isso são o fallback quando o código é desconhecido.

    Ordem de resolução:

    1. código no catálogo ⇒ frase PT-BR + ``(código N)`` para rastreio;
    2. código fora do catálogo ⇒ ``title``/``details`` da própria Meta,
       prefixados com o código quando houver;
    3. nada legível ⇒ o próprio código; sem código, :data:`UNKNOWN_REASON`.

    Nunca devolve string vazia e nunca levanta.
    """
    numeric = _coerce_code(code)
    # Código não-numérico (provider exótico) ainda serve como rótulo de rastreio.
    label = str(numeric) if numeric is not None else (
        str(code).strip() if code not in (None, "") else "")
    suffix = f" (código {label})" if label else ""

    hint = ERROR_HINTS.get(numeric) if numeric is not None else None
    if hint:
        return f"{hint}{suffix}"

    parts: list[str] = []
    for value in (title, details):
        text = (value or "").strip()
        # A Meta costuma repetir title == message e, às vezes, details == title.
        if text and text not in parts:
            parts.append(text)
    if parts:
        return f"Falha no envio{suffix}: {' — '.join(parts)}"
    if label:
        return f"Falha no envio{suffix}."
    return UNKNOWN_REASON
