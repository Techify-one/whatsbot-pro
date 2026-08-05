"""Categoria do arquivo × tipo da mensagem — o guarda do anexo (espelhado em `static/mediaKinds.js`).

Por que existe: os tipos `image`/`audio`/`video` viram exatamente esses `kind` no
`outbound_router.send_media` (`actions._MEDIA_KINDS`), e o provedor VALIDA o tipo declarado
contra o Content-Type do arquivo que ele baixa — um `.mp4` anexado numa mensagem de "Imagem"
não vira "quase certo", vira recusa do provedor na hora do disparo (ex.: a Meta responde
`(#100) … code 100/2018007`). O erro nasce na tela, meses antes, e só aparece no disparo:
o lugar barato de barrar é a tela + a rota.

Regra (deliberadamente conservadora): só bloqueia quando reconhece que o arquivo é de OUTRA
categoria. Arquivo que não dá pra classificar (sem extensão conhecida e com MIME genérico
tipo `application/octet-stream`) passa — quem julga aí é o provedor, com as regras dele
(`MediaLimits`). `document` aceita QUALQUER coisa de propósito: uma imagem, um vídeo ou um
PDF são todos documentos válidos quando o operador quer mandar como arquivo.
"""

from __future__ import annotations

import os

# Extensões que identificam a categoria com segurança. Não é a lista do que o provedor
# aceita (isso é `MediaLimits`, que é do canal) — é só o que basta para dizer "isto é um
# vídeo, não uma imagem".
EXTENSOES: dict[str, frozenset[str]] = {
    "image": frozenset({"jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "heif",
                        "tif", "tiff", "avif"}),
    "audio": frozenset({"mp3", "ogg", "oga", "opus", "m4a", "aac", "wav", "amr", "flac",
                        "wma"}),
    "video": frozenset({"mp4", "mov", "3gp", "3gpp", "m4v", "mkv", "webm", "avi", "wmv",
                        "mpeg", "mpg"}),
}

# Tipos de mensagem que exigem um arquivo daquela categoria. `document` fica de fora.
TIPOS_RESTRITOS = ("image", "audio", "video")

ROTULOS = {"image": "imagem", "audio": "áudio", "video": "vídeo", "document": "documento"}
ARTIGOS = {"image": "uma imagem", "audio": "um áudio", "video": "um vídeo"}

# O que o seletor de arquivo do navegador oferece por padrão (atributo `accept`).
ACCEPT = {"image": "image/*", "audio": "audio/*", "video": "video/*", "document": ""}


def extensao(nome: str | None) -> str:
    """Extensão em minúsculas, sem ponto (`""` quando não há)."""
    base = os.path.basename(str(nome or "").split("?")[0].split("#")[0])
    _, _, ext = base.rpartition(".")
    return ext.strip().lower() if ext and ext != base else ""


def categoria(nome: str | None = None, mime: str | None = None) -> str | None:
    """Categoria do arquivo (`image`/`audio`/`video`) ou ``None`` quando não dá pra dizer.

    O MIME manda quando é específico; um `application/octet-stream` (o que muitos
    navegadores mandam para extensões que não conhecem) não decide nada e cai na extensão.
    """
    tipo_mime = str(mime or "").strip().lower().split(";")[0]
    familia = tipo_mime.split("/")[0]
    if familia in EXTENSOES and tipo_mime != f"{familia}/":
        return familia
    ext = extensao(nome)
    if not ext:
        return None
    for kind, exts in EXTENSOES.items():
        if ext in exts:
            return kind
    return None


def combina(tipo: str, nome: str | None = None, mime: str | None = None) -> bool:
    """O arquivo serve para uma mensagem deste `tipo`?"""
    if tipo not in TIPOS_RESTRITOS:
        return True
    cat = categoria(nome, mime)
    return cat is None or cat == tipo


def erro_de_incompatibilidade(tipo: str, nome: str | None = None,
                              mime: str | None = None) -> str | None:
    """Mensagem PT-BR pronta para a UI/rota, ou ``None`` quando está tudo certo."""
    if combina(tipo, nome, mime):
        return None
    cat = categoria(nome, mime)
    arquivo = os.path.basename(str(nome or "")) or "o arquivo"
    return (f"A mensagem é do tipo “{ROTULOS.get(tipo, tipo)}”, mas {arquivo} é "
            f"{ARTIGOS.get(cat, cat)}. Troque o arquivo ou mude o tipo da mensagem "
            f"(“Documento” aceita qualquer formato).")
