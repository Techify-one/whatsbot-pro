"""Upload size/quantity ceilings (plano 64 · F2).

Until the media routes stream to disk (they still do ``await file.read()``, i.e.
the whole file lands in RAM — plano 64 P2), an unbounded upload is a trivial way
to take the server down. Starlette does not cap a file part and there is no
proxy-level ``client_max_body_size`` in the dev/Docker setup, so the ceiling is
enforced here.

Two layers, on purpose: the browser refuses oversized files instantly (good UX)
and this middleware refuses them again server-side (the client is bypassable).
"""

from __future__ import annotations

import re

from fastapi.responses import JSONResponse

# 50 MB/file, 10 files per drop. WhatsApp itself accepts ~16 MB of media and
# ~100 MB of document; 50 MB is the safe middle ground while uploads are still
# buffered in memory. Mirrored in web/static/js/services/uploadLimits.js.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_FILES_PER_DROP = 10

# Paths whose body is a file upload. Anchored on the route suffix so the
# per-phone prefix (and the sandbox equivalents) are covered by one pattern.
#
# ⚠️ **Uma rota de upload que não entre nesta regex NÃO TEM TETO NENHUM** — o
# corpo inteiro vai para a RAM do processo (as rotas ainda fazem
# ``await file.read()``). O gate é por LISTA DE CAMINHOS, então acrescentar rota
# aqui é parte de shipar a rota, não uma melhoria posterior. O grupo da v1 é
# separado de propósito: a fachada não tem o prefixo ``contacts/{phone}/`` e
# tentar "aproveitar" o grupo existente deixaria o caminho descoberto.
_UPLOAD_PATH_RE = re.compile(
    r"^/api/(?:"
    r"contacts/[^/]+/(?:send-image|send-audio|send-video|send-document"
    r"|private-audio|private-image|private-document)"
    r"|contacts/import"
    r"|sandbox/(?:send-image|send-audio|send-document)"
    r"|v1/messages/media"
    r")/?$"
)


def is_upload_path(path: str) -> bool:
    return bool(_UPLOAD_PATH_RE.match(path))


def too_large_message(limit_bytes: int = MAX_UPLOAD_BYTES) -> str:
    """O MESMO texto de :func:`too_large_response`, sem o envelope do painel.

    A fachada ``/api/v1`` tem DTO próprio (``{"error": {...}}``) e não pode
    devolver o ``{"ok": false}`` daqui; o que ela reaproveita é a frase — para
    o integrador ver o mesmo limite escrito do mesmo jeito nas duas superfícies.
    """
    return f"Arquivo excede o limite de {limit_bytes // (1024 * 1024)} MB."


def too_large_response(limit_bytes: int = MAX_UPLOAD_BYTES, *,
                       path: str = "") -> JSONResponse:
    """413 na forma que AQUELA superfície fala.

    O painel come ``{ok: false, error}``; a fachada ``/api/v1`` tem DTO próprio
    (``{"error": {code, message}}``). Este middleware é o ÚNICO ponto em que a v1
    responde sem passar pelo handler de ``V1Error`` — sem esta distinção, o
    integrador teria de escrever um caso especial só para o erro de tamanho.
    """
    message = too_large_message(limit_bytes)
    if path.startswith("/api/v1/"):
        return JSONResponse({"error": {"code": "too_big", "message": message}},
                            status_code=413)
    return JSONResponse({"ok": False, "error": message}, status_code=413)


def base64_exceeds(encoded: str, limit_bytes: int = MAX_UPLOAD_BYTES) -> bool:
    """True quando um payload base64 estoura o teto — SEM decodificar (plano 151 · I10).

    O caminho JSON (``content_base64``) **não passa** pelo middleware de upload:
    lá o corpo é JSON, não ``multipart``. Decodificar primeiro para depois medir
    já colocou o arquivo inteiro na RAM — que é exatamente o que o teto existe
    para impedir. base64 tem tamanho previsível (4 caracteres por 3 bytes menos o
    padding), então a recusa acontece pelo COMPRIMENTO DA STRING.

    A fronteira é a MESMA do multipart: os dois caminhos recusam no mesmo tamanho
    de arquivo, para o integrador não descobrir um limite diferente só por ter
    trocado a forma de envio. Espera a string já sem espaço em volta (a rota faz
    ``.strip()``); base64 quebrado em linhas conta os separadores e portanto é
    medido de forma CONSERVADORA — o que erra para o lado de recusar, nunca para
    o de estourar a RAM.
    """
    n = len(encoded)
    padding = 2 if encoded.endswith("==") else (1 if encoded.endswith("=") else 0)
    return (n // 4) * 3 + (n % 4) - padding > limit_bytes
