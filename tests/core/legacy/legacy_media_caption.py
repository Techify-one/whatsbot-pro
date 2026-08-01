"""Unit tests puros para a legenda de mídia (plano 87).

Sem DB, sem HTTP — só os helpers de ``db/repositories/_mapping.py`` que decidem
o que é texto DO CLIENTE e o que é texto GERADO PELA IA dentro do ``content``
composto de uma mensagem de mídia.

Espelho em Python do ``mediaCaptionOf``/``mediaPreviewLabel`` do frontend
(``web/static/js/services/messageView.js`` e ``messages.js``, cobertos por
``node --test``). As duas implementações descrevem o MESMO contrato e devem
mudar juntas — por isso os casos abaixo são os mesmos dos testes JS.

    venv/bin/python -m tests.core.legacy.legacy_media_caption
"""

import sys
from pathlib import Path
from tests.paths import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))

from db.repositories._mapping import caption_from_content, media_preview  # noqa: E402

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


# A descrição real de produção é markdown MULTILINHA — é justamente por isso que
# um corte posicional (split no primeiro/último "\n") não serve.
DESC = ("[Descrição da imagem]: duas janelas do WinBox abertas\n"
        "**Janela principal:**\n* linha de detalhe")
LEGENDA = "to pingando do roteador da contabilidade pro 8.8.8.8"


print("\ncaption_from_content — bloco da IA no INÍCIO (imagem/áudio):")
check("descrição pura → vazio", caption_from_content(DESC) == "")
check("descrição + legenda no fim → vazio (não adivinha)",
      caption_from_content(f"{DESC}\n{LEGENDA}") == "")
check("transcrição de áudio → vazio",
      caption_from_content("[Transcrição do áudio]: olá, quanto custa") == "")

print("\ncaption_from_content — bloco da IA no FIM (documento):")
check("corta no marcador exato",
      caption_from_content("segue o comprovante\n[Conteúdo do documento]: SICOOB\nR$ 898,30")
      == "segue o comprovante")
check("legenda multilinha do cliente é preservada",
      caption_from_content("linha um\nlinha dois\n[Conteúdo do documento]: dump")
      == "linha um\nlinha dois")
check("documento sem legenda → vazio",
      caption_from_content("[Conteúdo do documento]: dump") == "")

print("\ncaption_from_content — texto simples passa intacto:")
check("legenda comum", caption_from_content("veja essa foto") == "veja essa foto")
check("None → vazio", caption_from_content(None) == "")
check("vazio → vazio", caption_from_content("") == "")
check("rótulo de documento do GOWA passa (não é texto de IA)",
      caption_from_content("[Documento recebido: c.pdf]\nsegue")
      == "[Documento recebido: c.pdf]\nsegue")

print("\nmedia_preview — a lista de conversas nunca mostra texto da IA:")
check("imagem descrita → rótulo (antes vazava a descrição)",
      media_preview(DESC, "image") == "\U0001f4f7 Imagem")
check("imagem descrita COM legenda → a legenda",
      media_preview(f"{DESC}\n{LEGENDA}", "image", LEGENDA) == LEGENDA)
check("imagem sem nada → rótulo", media_preview("", "image") == "\U0001f4f7 Imagem")
check("imagem só com legenda (sem IA) → a legenda",
      media_preview("minha foto", "image") == "minha foto")
check("áudio → rótulo fixo",
      media_preview("[Transcrição do áudio]: oi", "audio") == "\U0001f3a4 Áudio")
check("documento com extração e sem legenda → rótulo",
      media_preview("[Conteúdo do documento]: SICOOB", "document") == "\U0001f4c4 Documento")
check("documento com legenda → a legenda",
      media_preview("comprovante\n[Conteúdo do documento]: SICOOB", "document") == "comprovante")

print("\nmedia_preview — caminhos NÃO tocados pelo plano 87:")
check("texto puro → content[:80]", media_preview("oi tudo bem", None) == "oi tudo bem")
check("texto longo cortado em 80", len(media_preview("x" * 200, None)) == 80)
check("sem mensagem visível (None) → vazio", media_preview(None, "image") == "")
check("vídeo segue no caminho legado", media_preview("veja isso", "video") == "veja isso")
check("legenda cortada em 80",
      len(media_preview("y" * 200, "image", "y" * 200)) == 80)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
