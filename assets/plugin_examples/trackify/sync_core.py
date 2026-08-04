"""Motor de decisão da sincronização. PURO: sem banco, sem rede, sem relógio.

Recebe um mapeamento, o estado da última convergência e os valores ATUAIS dos
dois lados, e devolve o que fazer. Os três caminhos que disparam sincronização
(evento, poll e varredura de reconciliação) chamam esta mesma função — é o que
garante que os três não desenvolvam regras ligeiramente diferentes.

Estado guarda HASH, nunca valor (ver ``field_codec.hash_value``), e distingue
``None`` (nunca observamos este par) de ``""`` (observamos, e estava vazio). Só
essa diferença separa "foi apagado lá" de "nunca sincronizamos", e é ela que
impede a primeira execução de sair apagando campo no CRM do cliente.
"""

from __future__ import annotations

from dataclasses import dataclass

from .field_codec import hash_value, normalize_for_hash

PUSH = "push"           # escrever no Trackify
PULL = "pull"           # escrever no WhatsBot
RESTAMP = "restamp"     # nada a escrever, só atualizar os hashes do estado
NOOP = "noop"           # nem isso
CONFLICT = "conflict"   # os dois lados mudaram e a política mandou segurar

DIR_TO_TK = "to_trackify"
DIR_TO_WB = "to_whatsbot"
DIR_BOTH = "both"

POLICY_WB = "whatsbot_wins"
POLICY_TK = "trackify_wins"
POLICY_HOLD = "hold"


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str = ""
    wb_hash: str = ""
    tk_hash: str = ""


def decide(mapping: dict, state: dict | None, wb_value, tk_value) -> Decision:
    """Compara os dois lados e devolve a ação.

    ``state`` ausente (``None``) é a PRIMEIRA VISÃO do par — tratada como caso
    próprio, nunca como conflito: sem hashes anteriores não há como saber quem
    mudou, e chamar isso de conflito faria toda ativação da feature nascer com
    a tela cheia de conflitos falsos.
    """
    # Normaliza ANTES de hashear: "está vazio?" se pergunta ao valor, não ao
    # hash — ``hash_value("")`` é um sha1, e portanto sempre truthy.
    wb_norm = normalize_for_hash(wb_value)
    tk_norm = normalize_for_hash(tk_value)
    wb_h = hash_value(wb_norm)
    tk_h = hash_value(tk_norm)
    st = state or {}

    direction = (mapping.get("direction") or DIR_TO_TK).strip()
    can_push = direction in (DIR_TO_TK, DIR_BOTH)
    can_pull = direction in (DIR_TO_WB, DIR_BOTH)

    def result(action: str, reason: str = "") -> Decision:
        return Decision(action, reason, wb_h, tk_h)

    # Um valor do CDP que a validação do WhatsBot já recusou não pode ser
    # redetectado a cada ciclo — sem esta guarda a varredura acha a MESMA
    # divergência para sempre e o contador de recusa cresce sem fim.
    # Guardado à PARTE de ``can_pull``: um valor recusado não pode transformar
    # um mapeamento de mão dupla em mão única aos olhos da regra de conflito.
    pull_blocked = bool(tk_norm) and tk_h == (st.get("rejected_hash") or "")
    may_pull = can_pull and not pull_blocked

    prev_wb = st.get("wb_hash")
    prev_tk = st.get("tk_hash")
    first_sight = prev_wb is None and prev_tk is None

    # ── Os dois lados já concordam ───────────────────────────────────────
    if wb_h == tk_h:
        if prev_wb == wb_h and prev_tk == tk_h:
            return result(NOOP)
        # Convergiram (por nós ou sozinhos): só carimbar. Vale inclusive
        # quando os DOIS mudaram para o mesmo valor — isso não é conflito.
        return result(RESTAMP, "lados iguais")

    # ── Primeira visão: semear, nunca apagar ─────────────────────────────
    if first_sight:
        if wb_norm and can_push:
            return result(PUSH, "primeira sincronização")
        if tk_norm and may_pull:
            return result(PULL, "primeira sincronização")
        # O único lado com valor é o que não podemos escrever a partir daqui.
        return result(RESTAMP, "primeira sincronização (direção não permite)")

    # Lado nunca observado isoladamente conta como "não mudou": é a leitura
    # conservadora, e a alternativa (chamar de mudança) inventaria conflito.
    base_wb = prev_wb if prev_wb is not None else wb_h
    base_tk = prev_tk if prev_tk is not None else tk_h
    wb_changed = wb_h != base_wb
    tk_changed = tk_h != base_tk

    # ── Conflito de verdade: os dois mudaram, para valores diferentes ────
    if wb_changed and tk_changed:
        # Mão única NÃO conflita, por construção: só um lado pode escrever, e
        # portanto não há disputa a arbitrar — o lado autoritativo manda.
        if not (can_push and can_pull):
            if can_push:
                return result(PUSH, "mudou dos dois lados (mão única: WhatsBot manda)")
            if may_pull:
                return result(PULL, "mudou dos dois lados (mão única: Trackify manda)")
            return result(RESTAMP, "mudou dos dois lados (direção não permite escrever)")

        policy = (mapping.get("conflict_policy") or POLICY_WB).strip()
        if policy == POLICY_WB:
            return result(PUSH, "conflito: WhatsBot vence")
        if policy == POLICY_TK and may_pull:
            return result(PULL, "conflito: Trackify vence")
        return result(CONFLICT, "os dois lados mudaram desde a última sincronização")

    if wb_changed:
        return result(PUSH, "mudou no WhatsBot") if can_push else result(
            RESTAMP, "mudou no WhatsBot (direção não permite escrever no Trackify)")

    if tk_changed:
        return result(PULL, "mudou no Trackify") if may_pull else result(
            RESTAMP, "mudou no Trackify (direção não permite escrever no WhatsBot)")

    # ── Divergem, mas nenhum lado mudou desde o último carimbo ───────────
    # Significa que a última tentativa de convergir não aterrissou (push que
    # falhou, valor recusado, mapeamento recém-ligado sobre dados antigos).
    # Re-tenta na direção permitida em vez de ficar parado divergindo.
    if can_push:
        return result(PUSH, "divergência pendente")
    if may_pull:
        return result(PULL, "divergência pendente")
    return result(NOOP, "divergência fora da direção configurada")
