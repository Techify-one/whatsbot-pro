"""System-prompt assembly for the agent turn.

Extracted from ``agent.handler`` (Plano 23 · Fase B5, master §2.3/§2.4). Builds
the PT-BR system prompt in named sections, layering the dynamic context on top of
the DB-resolved agent ``base_prompt``:

* group context + @mention roster (groups only),
* known contact info (incl. custom attributes via ``ContactMemory.get_info_summary``),
* contact tags,
* operator-message / private-note handling instructions,
* plugin-contributed prompt fragments (isolated per plugin),
* current date/time (BRT),
* the ``split_messages`` JSON-array output format (when on).

``AgentHandler._build_system_prompt`` delegates here. ``filter.system_prompt`` is
applied by the caller (``agent_run_service`` / the handler), NOT here — this
module only assembles the base text.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from agent import group_mentions
from plugins.context import PromptContext, get_channel_runtime

logger = logging.getLogger(__name__)


def _channel_supports_groups(channel_id: str | None) -> bool:
    """Whether ``channel_id``'s provider supports groups (plano 38 F7).

    Capability-driven via the wired ``outbound_router`` (never ``if provider ==``).
    Fail-open to ``True`` when the runtime isn't wired (legacy/tests) or the channel
    is unknown — preserves the previous behavior (GOWA is the default) so nothing
    that worked before goes silent; only a channel that explicitly declares
    ``groups=False`` (Telegram/Cloud) is gated out."""
    _registry, outbound, _ingest = get_channel_runtime()
    if outbound is None:
        return True
    cid = channel_id or "default"
    inst = outbound.get(cid)
    if inst is None:
        return True
    return outbound.supports(cid, "groups")


def build_system_prompt(handler, contact, base_prompt: str | None = None,
                        split_messages: bool | None = None) -> str:
    """Build system prompt with contact info and current date/time injected.

    ``base_prompt`` is the DB-resolved agent prompt (config-in-DB); the dynamic
    sections (group context, contact info, tags, date, plugin fragments,
    split-messages format) layer on top. ``split_messages`` overrides
    ``handler.split_messages`` (per-channel resolution, plano 21).
    """
    split_on = handler.split_messages if split_messages is None else split_messages
    prompt = base_prompt if base_prompt is not None else ""
    if contact.is_group:
        gname = f" chamado '{contact.group_name}'" if contact.group_name else ""
        prompt += (
            f"\n\n--- Contexto de grupo ---\n"
            f"Esta é uma conversa de grupo{gname}.\n"
            "As mensagens de usuários estão no formato '[Nome]: mensagem'.\n"
            "Quando responder, leve em conta quem fez a pergunta e responda "
            "de forma natural ao grupo.\n"
            "--- Fim do contexto de grupo ---"
        )
        # Inject participant names so the AI can @mention a specific member — só para
        # canais cujo provider suporta grupos (GOWA). plano 38 F7: um grupo de outro
        # provider (Telegram/Cloud) não dispara o get_group_info do GOWA com um id que
        # o GOWA não resolve; sem capability → lista de membros vazia (fallback atual).
        members = []
        if _channel_supports_groups(getattr(contact, "channel_id", None)):
            try:
                members = group_mentions.get_members(contact.phone)
            except Exception:
                members = []
        named = [m["name"] for m in members if m.get("name")]
        if named:
            prompt += (
                "\n\n--- Mencionar participantes ---\n"
                "Para mencionar alguém do grupo, escreva @ seguido do nome "
                "EXATAMENTE como aparece nesta lista — o sistema converte "
                "automaticamente para a menção real do WhatsApp:\n"
                + ", ".join(f"@{n}" for n in named)
                + "\nMencione apenas quando fizer sentido se dirigir a uma "
                "pessoa específica; não mencione todo mundo sem necessidade.\n"
                "--- Fim mencionar participantes ---"
            )
    info_summary = contact.get_info_summary()
    if info_summary:
        prompt += (
            f"\n\n--- Informações já conhecidas sobre este contato ({contact.phone}) ---\n"
            f"{info_summary}\n"
            "IMPORTANTE: Use estas informações na conversa. "
            "NÃO pergunte dados que já estão listados acima (ex: nome, email, etc). "
            "NÃO chame save_contact_info para dados que já aparecem nesta seção — "
            "eles já estão salvos. Só use save_contact_info quando o usuário revelar "
            "informação NOVA na mensagem mais recente.\n"
            "--- Fim das informações ---"
        )
    tags_summary = contact.get_tags_summary()
    if tags_summary:
        prompt += (
            f"\n\n--- Tags do contato ---\n"
            f"{tags_summary}\n"
            "Estas tags foram aplicadas por operadores humanos para classificar este contato. "
            "Use-as como sinal de contexto sobre o histórico/perfil do contato, mas não as "
            "mencione diretamente na conversa nem peça confirmação sobre elas.\n"
            "--- Fim das tags ---"
        )
    prompt += (
        "\n\nMensagens marcadas com '[Mensagem do operador humano]' no histórico "
        "foram enviadas por um atendente real, não por você. Considere o contexto "
        "mas não imite o estilo do operador."
        "\n\nMensagens marcadas com '[Nota privada do operador]' são notas "
        "internas do painel — o contato NUNCA as viu. Quando o operador "
        "acionar você sobre uma nota (via toggle 'IA lê' no painel), trate-a "
        "como instrução direta para executar: use as tools disponíveis (ex.: "
        "criar lembrete, salvar informações do contato, etc.) se a instrução "
        "pedir, e redija a resposta ao contato. Caso contrário, use as notas "
        "apenas como contexto, sem citar, mencionar ou parafrasear em "
        "respostas ao contato."
    )

    # Plugin-contributed prompt fragments. Each fragment is a callable that
    # receives (contact, PromptContext) and returns a string (or empty).
    # Errors are isolated so a buggy plugin can't kill the request.
    for fragment_fn, plugin_id in handler._prompt_fragments:
        try:
            ctx = PromptContext(handler=handler, plugin_id=plugin_id)
            chunk = fragment_fn(contact, ctx)
            if chunk:
                prompt += chunk
        except Exception as e:
            logger.warning(
                "Plugin %s prompt fragment failed: %s",
                plugin_id or "?", e,
            )
    _BRT = timezone(timedelta(hours=-3))
    now = datetime.now(_BRT)
    dias = ["segunda-feira", "terça-feira", "quarta-feira",
            "quinta-feira", "sexta-feira", "sábado", "domingo"]
    prompt += (
        f"\n\n--- Data e hora atual ---\n"
        f"Data: {now.strftime('%d/%m/%Y')} ({dias[now.weekday()]})\n"
        f"Hora: {now.strftime('%H:%M')}\n"
        "--- Fim ---"
    )
    if split_on:
        prompt += (
            "\n\n--- FORMATO DE SAÍDA (OBRIGATÓRIO) ---\n"
            "Sua resposta INTEIRA deve ser UM array JSON de strings — nada mais.\n"
            "O PRIMEIRO caractere da sua saída é '[' e o ÚLTIMO é ']'.\n"
            "Cada string do array vira uma mensagem separada no WhatsApp.\n"
            "\n"
            "PROIBIDO (quebra o sistema):\n"
            "- Escrever qualquer coisa fora do array (sem texto antes do '[' nem depois do ']').\n"
            "- Usar separadores entre mensagens: nada de '---', 'response', '\\n\\n', bullets, números.\n"
            "  A ÚNICA forma de separar mensagens é criar outro elemento (string) no MESMO array.\n"
            "- Retornar vários arrays. É sempre UM único array, com um único '[' e um único ']'.\n"
            "- Markdown, títulos ou formatação especial dentro das strings.\n"
            "\n"
            "Como dividir:\n"
            "- Cada mensagem deve ser CURTA (1 a 3 linhas). Mensagens grandes ficam horríveis.\n"
            "- Resposta simples = array com 1 elemento só. Não separe a saudação do conteúdo curto.\n"
            "- Dados distintos (link, chave PIX, valor, instrução) = cada um em seu próprio elemento.\n"
            "\n"
            "EXEMPLO de como a sua resposta DEVE ser.\n"
            "Se você fosse mandar isto (NÃO faça assim, é uma só mensagem gigante):\n"
            "  Ótimo! Já vou te mandar o link do e-book.\n"
            "  https://exemplo.com/ebook\n"
            "  Faça o PIX de R$27 pra liberar o acesso.\n"
            "  Chave: f765ce68-49ba-4c08-9624-8b1fd63779b2\n"
            "  Aparece como: Techify\n"
            "  Quando pagar, me manda o print que eu confiro.\n"
            "Você DEVE responder assim (array JSON, cada parte uma mensagem curta):\n"
            '[\"Ótimo! Já vou te mandar o link do e-book.\", '
            '\"https://exemplo.com/ebook\", '
            '\"Faça o PIX de R$27 pra liberar o acesso.\", '
            '\"Chave: f765ce68-49ba-4c08-9624-8b1fd63779b2\", '
            '\"Aparece como: Techify\", '
            '\"Quando pagar, me manda o print que eu confiro.\"]\n'
            "\n"
            "Mais exemplos:\n"
            'Resposta curta: [\"Ok, só um minuto!\"]\n'
            'Resposta média: [\"Sobre o plano mensal:\", \"São R$99 e inclui X, Y e Z.\", \"Quer o link?\"]\n'
            "--- FIM DO FORMATO ---"
        )
    return prompt
