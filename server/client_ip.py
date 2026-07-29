"""Resolução do IP do CLIENTE (navegador) atrás de proxy reverso.

Ponto ÚNICO onde o core decide "de qual IP veio esta request". Consumidores: o
carimbo de ator da trilha de auditoria (``server/app.py``, via ``audit_ip``) e o
bucket de rate-limit do login (``server/routes/auth.py``, via ``client_ip``).

Duas funções, de propósito SEPARADAS (plano 86 · D4): ``client_ip()`` é a
verdade de rede, não-forjável, e serve a decisões de segurança;
``audit_ip()`` prefere o IP público que o painel declara num cabeçalho
(autodeclarado, forjável) e serve SÓ à auditoria. Não unifique as duas — ver
``reported_public_ip`` no fim do arquivo.

O problema: com um proxy reverso na frente (Coolify/Traefik/nginx), o peer TCP
é o proxy, não o navegador. O IP real vem no ``X-Forwarded-For``, uma lista da
esquerda (mais perto do cliente) para a direita (mais perto de nós) — e a parte
da ESQUERDA é escrita pelo próprio chamador, ou seja, FORJÁVEL. Pegar
``xff.split(",")[0]`` (o que o core fazia) aceita qualquer IP que o cliente
queira inventar: envenena a auditoria e deixa furar o rate-limit de login
rodando um XFF diferente a cada tentativa.

A regra correta é caminhar da DIREITA para a ESQUERDA pulando os hops que são
proxies confiáveis; o primeiro que não é confiável é o cliente. Entradas
forjadas ficam sempre à esquerda do endereço que o proxy anexou, então nunca
são escolhidas. Mesma convenção já usada pelos plugins ``protocolos`` e
``website``.

Configuração (env, ambas opcionais):

``WHATSBOT_TRUSTED_PROXIES``
    Lista de IPs/CIDRs separados por vírgula que SUBSTITUI o conjunto padrão de
    proxies confiáveis (loopback + faixas privadas + link-local + CGNAT).
    String vazia = não confiar em ninguém (usa sempre o peer TCP).

``WHATSBOT_TRUSTED_PROXY_HOPS``
    Número EXATO de proxies entre o navegador e o app. Quando definido (> 0),
    vence a heurística acima: o cliente é a entrada N hops à esquerda do peer.
    É o modo à prova de falsificação mesmo quando o navegador está numa faixa
    privada (acesso por VPN/LAN, onde "privado" não distingue proxy de cliente).
"""

from __future__ import annotations

import ipaddress
import logging
import os

logger = logging.getLogger(__name__)

# Loopback, faixas privadas (RFC1918), link-local, ULA IPv6 e CGNAT — onde um
# proxy reverso costuma viver. Não é uma lista de "IPs inválidos": é a lista de
# hops cuja palavra sobre quem é o cliente não conta.
_DEFAULT_TRUSTED = (
    "127.0.0.0/8", "::1/128",
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "fe80::/10", "fc00::/7",
    "100.64.0.0/10",
)


def _parse_networks(spec: str) -> tuple:
    nets = []
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("WHATSBOT_TRUSTED_PROXIES: entrada inválida ignorada (%s).", item)
    return tuple(nets)


def _trusted_networks() -> tuple:
    spec = os.environ.get("WHATSBOT_TRUSTED_PROXIES")
    if spec is None:
        return _parse_networks(",".join(_DEFAULT_TRUSTED))
    return _parse_networks(spec)


def _trusted_hops() -> int:
    raw = os.environ.get("WHATSBOT_TRUSTED_PROXY_HOPS", "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("WHATSBOT_TRUSTED_PROXY_HOPS inválido (%s) — ignorado.", raw)
        return 0


def normalize_ip(raw: str) -> str | None:
    """Normaliza uma entrada de header em IP, ou ``None`` se não for um IP.

    Tolera o que os proxies escrevem na prática: espaço em volta, ``[::1]:443``
    e ``1.2.3.4:5678``. Entrada que não vira IP é DESCARTADA (não pode ser um
    cliente real, e aceitá-la seria aceitar lixo forjado).
    """
    item = (raw or "").strip()
    if not item:
        return None
    if item.startswith("["):                      # [ipv6] ou [ipv6]:porta
        item = item[1:].split("]", 1)[0]
    elif item.count(":") == 1:                    # ipv4:porta (ipv6 tem vários)
        item = item.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(item))
    except ValueError:
        return None


def _is_trusted(ip: str, nets: tuple) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in nets)


def forwarded_chain(request) -> list[str]:
    """Cadeia de IPs da request, do mais próximo do cliente ao peer TCP.

    ``X-Forwarded-For`` (ou ``X-Real-IP``, quando não há XFF) + o peer TCP no
    fim. Entradas ilegíveis somem.
    """
    headers = request.headers
    raw = headers.get("x-forwarded-for", "") or headers.get("x-real-ip", "")
    chain = [ip for ip in (normalize_ip(p) for p in raw.split(",")) if ip]
    peer = normalize_ip(request.client.host) if request.client else None
    if peer:
        chain.append(peer)
    return chain


def client_ip(request) -> str | None:
    """IP do navegador que originou a request, ou ``None`` se indeterminável."""
    chain = forwarded_chain(request)
    if not chain:
        return None

    hops = _trusted_hops()
    if hops:
        # Contagem exata: o cliente está `hops` posições à esquerda do peer.
        # Cadeia mais curta que o esperado = alguém removeu hops; fica no que
        # sobrou à esquerda em vez de devolver um proxy.
        return chain[max(0, len(chain) - 1 - hops)]

    nets = _trusted_networks()
    for candidate in reversed(chain):
        if not _is_trusted(candidate, nets):
            return candidate
    # Todo hop é confiável: ou a instalação é só LAN/VPN (e aí o de mais à
    # esquerda É o navegador), ou o IP real se perdeu antes de chegar aqui.
    return chain[0]


# ── IP autodeclarado pelo painel (plano 86) ─────────────────────────────────
#
# Quando o IP de origem morre num hop ANTES do proxy reverso, a cadeia XFF
# inteira chega privada e NENHUMA resolução de rede o recupera (medido: LAN,
# internet pública e o painel real da operadora chegam idênticos). A saída é
# inverter o sentido: o navegador descobre o próprio IP público
# (web/static/js/services/publicIp.js) e o manda neste cabeçalho.
REPORTED_IP_HEADER = "x-client-public-ip"


def reported_public_ip(request) -> str | None:
    """IP público que o NAVEGADOR declarou, ou ``None`` se ausente/inválido.

    ⚠️ Valor AUTODECLARADO — logo, forjável. Isso está aceito (decisão D1 do
    plano 86: "a maioria dos usuários é leiga e não pensará em fraudar") e vale
    APENAS para a trilha de auditoria. NUNCA use este valor para uma decisão de
    segurança — em especial o bucket de rate-limit do login (D4): bastaria variar
    o cabeçalho a cada tentativa para anular o limite de falhas.

    A única validação é contra LIXO: exige um IP **global**, o que recusa
    privado, loopback, link-local, CGNAT, multicast e texto qualquer. Recusado ⇒
    ``None`` ⇒ o chamador cai no IP observado na rede.
    """
    ip = normalize_ip(request.headers.get(REPORTED_IP_HEADER, ""))
    if not ip:
        return None
    try:
        if not ipaddress.ip_address(ip).is_global:
            return None
    except ValueError:                                # normalize_ip já garante, cinto de segurança
        return None
    return ip


def audit_ip(request) -> str | None:
    """IP a gravar na trilha de auditoria: o declarado pelo painel, senão a rede.

    É o ÚNICO consumidor de ``reported_public_ip``. O rate-limit de login usa
    ``client_ip`` direto e não deve migrar para cá (D4) — ver a docstring acima.
    """
    return reported_public_ip(request) or client_ip(request)
