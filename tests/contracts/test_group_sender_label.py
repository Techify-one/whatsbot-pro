"""O nome de quem enviou uma MÍDIA em grupo sobrevive até o painel.

Não existe coluna de remetente em ``messages``: o autor de uma mensagem de grupo
viaja embutido no ``content``, como o prefixo ``"[Fulano]: "`` que o painel
extrai de volta (``stripGroupPrefix`` em ``web/static/js/services/composerTokens.js``)
para desenhar o cabeçalho da bolha. Quando esse prefixo não está lá, o rótulo cai
no ``displayName`` — que, num grupo, é o NOME DO GRUPO.

Dois furos fechados aqui, os dois específicos de IMAGEM:

1. **Imagem recebida sem legenda não gerava texto nenhum.** Áudio, vídeo, sticker
   e documento ganham um placeholder em ``_extract_media`` (``"[Áudio recebido]"``
   & cia.), então o prefixo sempre tinha em que se pendurar; a imagem só
   preenchia o texto se houvesse legenda (ou se fosse ``is_from_me``). Resultado:
   ``content=''`` e a bolha assinada com o nome do grupo.

2. **A descrição da IA engolia o autor.** ``format_media_content`` junta a
   imagem prefix-first (``"<bloco da IA>\\n<texto>"``, ao contrário de
   áudio/documento), então o ``"[Fulano]: "`` ia parar na 2ª linha e o painel
   passava a ler *"Descrição da imagem"* como se fosse o remetente.
"""

from db.repositories._mapping import caption_from_content, media_preview
from gowa.inbound import _parse_message
from server.transcription import format_media_content, split_sender_prefix

GROUP = dict(chat_jid="120363429626999617@g.us",
             sender_jid="5511999999999@s.whatsapp.net",
             from_name="Luísa Maira", id="ABC123")


def _parse(**extra):
    events = _parse_message({**GROUP, **extra}, channel_id="c1", client=None,
                            bot_phone="5511888888888", bot_name="Bot",
                            group_mode="mention_only")
    assert events, "o parser descartou a mensagem"
    return events[0]


# ── split_sender_prefix ──────────────────────────────────────────────

def test_split_separa_o_carimbo_de_autor():
    assert split_sender_prefix("[Luísa Maira]: oi") == ("[Luísa Maira]: ", "oi")
    assert split_sender_prefix("[Luísa Maira]: ") == ("[Luísa Maira]: ", "")


def test_split_nao_confunde_rotulo_de_midia_com_autor():
    # O casamento exige "]: " — estes NÃO são carimbos de autor.
    for texto in ("[Documento recebido: a.pdf]", "[Imagem enviada]",
                  "[Áudio recebido]", "sem prefixo nenhum", ""):
        assert split_sender_prefix(texto) == ("", texto)


# ── inbound: o prefixo existe mesmo sem legenda ──────────────────────

def test_imagem_de_grupo_sem_legenda_carrega_o_autor():
    # Era ``''`` — o furo que fazia a bolha assinar com o nome do grupo.
    assert _parse(image={"path": "statics/media/x.jpg"}).display_text == "[Luísa Maira]: "


def test_midia_de_grupo_com_legenda_e_texto_seguem_iguais():
    assert (_parse(image={"path": "statics/media/x.jpg", "caption": "olha isso"})
            .display_text == "[Luísa Maira]: olha isso")
    assert _parse(body="bom dia").display_text == "[Luísa Maira]: bom dia"
    assert (_parse(audio={"path": "statics/media/a.ogg"}).display_text
            == "[Luísa Maira]: [Áudio recebido]")


def test_conversa_individual_nao_ganha_carimbo():
    event = _parse_message(
        {"chat_jid": "5511999999999@s.whatsapp.net",
         "sender_jid": "5511999999999@s.whatsapp.net",
         "from_name": "Luísa", "id": "X",
         "image": {"path": "statics/media/x.jpg"}},
        channel_id="c1", client=None, bot_phone="", bot_name="",
        group_mode="mention_only")[0]
    assert event.display_text == ""


# ── a descrição da IA não pode empurrar o autor para a 2ª linha ──────

def test_descricao_da_imagem_mantem_o_autor_na_frente():
    prefix, rest = split_sender_prefix("[Luísa Maira]: legenda")
    assert (format_media_content("image", "desc", rest, sender_prefix=prefix)
            == "[Luísa Maira]: [Descrição da imagem]: desc\nlegenda")

    prefix, rest = split_sender_prefix("[Luísa Maira]: ")
    assert (format_media_content("image", "desc", rest, sender_prefix=prefix)
            == "[Luísa Maira]: [Descrição da imagem]: desc")


def test_format_media_content_sem_prefixo_inalterado():
    # Conversa individual (e todo call site que não passa ``sender_prefix``).
    assert format_media_content("image", "desc", "legenda") == "[Descrição da imagem]: desc\nlegenda"
    assert format_media_content("image", "desc") == "[Descrição da imagem]: desc"
    assert format_media_content("audio", "trans") == "[Transcrição do áudio]: trans"
    assert (format_media_content("document", "conteudo", "[Nome]: [Documento recebido: a.pdf]")
            == "[Nome]: [Documento recebido: a.pdf]\n[Conteúdo do documento]: conteudo")


# ── o carimbo não pode vazar para a lista de conversas ───────────────

def test_preview_nao_vaza_o_carimbo_nem_a_descricao():
    assert media_preview("[Luísa Maira]: ", "image", None) == "\U0001f4f7 Imagem"
    assert (media_preview("[Luísa Maira]: [Descrição da imagem]: uma foto", "image", None)
            == "\U0001f4f7 Imagem")
    # A legenda do cliente (coluna do plano 87) continua tendo precedência.
    assert (media_preview("[Luísa Maira]: [Descrição da imagem]: uma foto", "image", "olha isso")
            == "olha isso")


def test_texto_de_grupo_segue_mostrando_quem_falou():
    # Só o bloco da IA (ou o carimbo sozinho) é descontado — texto normal, não.
    assert caption_from_content("[Luísa Maira]: bom dia") == "[Luísa Maira]: bom dia"
