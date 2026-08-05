"""Motor de regras — PURO (sem DB, sem rede, sem relógio implícito).

Porte de `filtros-engine.service.ts` (Nexus) com as correções travadas do plano 76:

* **Aninhamento arbitrário** (D4): uma lista de regras é avaliada da ESQUERDA para a
  DIREITA (`acc = conector == 'OU' ? acc or r : acc and r`, sem precedência — grupos são
  os parênteses). Um `grupo` recursa e vira UM booleano. Profundidade livre.
* **`between` em hora cruza a meia-noite** (D7): `min > max` ⇒ `v >= min OR v <= max`.
* **Fuso é offset FIXO** (D9): vem do contexto (`__tz_offset_hours`), nunca do processo.
* Árvore vazia ⇒ **true** (igual ao Nexus: configuração sem filtro sempre passa).
* Campo **desconhecido** ⇒ condição **false** (import de árvore de outro sistema não quebra).

O módulo `static/rules.js` é o espelho 1:1 disto (usado no preview da UI) — mudar um
operador aqui obriga a mudar lá, e os testes dos dois usam os mesmos vetores.

Estrutura da árvore (idêntica ao Nexus, para import/export compatível)::

    Arvore   = {"regras": [Regra, ...]}
    Regra    = Condicao | Grupo
    Condicao = {"tipo": "condicao", "conector"?: "E"|"OU", "campo", "operador", "valor"}
    Grupo    = {"tipo": "grupo",    "conector"?: "E"|"OU", "regras": [Regra, ...]}
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import catalog

logger = logging.getLogger("plugin.retornos")

MINUTO = 60.0

# Chaves de contexto reservadas (não são campos do catálogo).
CTX_NOW = "__now"
CTX_TZ = "__tz_offset_hours"
CTX_LAST_CLIENT = "__last_client_ts"


# ── Avaliação ─────────────────────────────────────────────────────────────────

def avaliar(arvore, ctx: dict) -> bool:
    """Avalia uma árvore ``{"regras": [...]}``. Árvore vazia/inválida ⇒ ``True``."""
    if not isinstance(arvore, dict):
        return True
    return avaliar_regras(arvore.get("regras") or [], ctx)


def avaliar_regras(regras, ctx: dict) -> bool:
    """Combinação esquerda→direita. A PRIMEIRA regra ignora o próprio ``conector``."""
    if not isinstance(regras, (list, tuple)) or not regras:
        return True
    acc: bool | None = None
    for regra in regras:
        try:
            valor = _avaliar_regra(regra, ctx)
        except Exception:  # noqa: BLE001 — regra malformada nunca derruba o ciclo
            logger.debug("retornos: regra malformada ignorada como falsa: %r", regra,
                         exc_info=True)
            valor = False
        if acc is None:
            acc = valor
            continue
        conector = str((regra or {}).get("conector") or "E").upper()
        acc = (acc or valor) if conector in ("OU", "OR") else (acc and valor)
    return bool(acc)


def _avaliar_regra(regra, ctx: dict) -> bool:
    if not isinstance(regra, dict):
        return False
    if str(regra.get("tipo") or "").lower() == "grupo":
        return avaliar_regras(regra.get("regras") or [], ctx)
    return avaliar_condicao(regra, ctx)


def avaliar_condicao(cond: dict, ctx: dict) -> bool:
    campo_id = str(cond.get("campo") or "")
    definicao = catalog.campo(campo_id)
    if definicao is None:
        return False  # campo desconhecido ⇒ falso (igual ao Nexus)
    operador = str(cond.get("operador") or "").lower()
    return aplicar_operador(operador, ctx.get(campo_id), cond.get("valor"),
                            definicao.get("tipo", "text"), ctx)


# ── Operadores ────────────────────────────────────────────────────────────────

def aplicar_operador(operador: str, atual, referencia, tipo: str, ctx: dict | None = None) -> bool:
    ctx = ctx or {}
    if operador == "exists":
        return _preenchido(atual)
    if operador == "not_exists":
        return not _preenchido(atual)

    if operador in ("has_any", "has_all", "in", "not_in"):
        lista_atual = _as_list(atual)
        lista_ref = _as_list(referencia)
        if operador == "has_any":
            return any(v in lista_atual for v in lista_ref)
        if operador == "has_all":
            return bool(lista_ref) and all(v in lista_atual for v in lista_ref)
        if operador == "in":
            return _norm_str(atual) in lista_ref
        return _norm_str(atual) not in lista_ref  # not_in

    if operador in ("contains", "not_contains", "starts_with", "ends_with"):
        a = _norm_str(atual)
        b = _norm_str(referencia)
        if operador == "contains":
            return bool(b) and b in a
        if operador == "not_contains":
            return not (bool(b) and b in a)
        if operador == "starts_with":
            return bool(b) and a.startswith(b)
        return bool(b) and a.endswith(b)  # ends_with

    if operador == "between":
        minimo, maximo = _par(referencia)
        a = _coerce(atual, tipo, ctx)
        lo = _coerce(minimo, tipo, ctx)
        hi = _coerce(maximo, tipo, ctx)
        if a is None or lo is None or hi is None:
            return False
        if tipo == "time" and lo > hi:
            return a >= lo or a <= hi  # D7 — atravessa a meia-noite
        return lo <= a <= hi

    if operador in ("gt", "gte", "lt", "lte"):
        a = _coerce(atual, tipo, ctx)
        b = _coerce(referencia, tipo, ctx)
        if a is None or b is None:
            return False
        if operador == "gt":
            return a > b
        if operador == "gte":
            return a >= b
        if operador == "lt":
            return a < b
        return a <= b

    if operador in ("eq", "neq"):
        igual = _iguais(atual, referencia, tipo, ctx)
        return igual if operador == "eq" else not igual

    return False  # operador desconhecido ⇒ falso


def _iguais(atual, referencia, tipo: str, ctx: dict) -> bool:
    if tipo in ("number", "time", "date"):
        a = _coerce(atual, tipo, ctx)
        b = _coerce(referencia, tipo, ctx)
        if a is None or b is None:
            return False
        return a == b
    if tipo == "multi-select":
        return _norm_str(referencia) in _as_list(atual)
    return _norm_str(atual) == _norm_str(referencia)


# ── Normalização de valores ───────────────────────────────────────────────────

def _preenchido(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (list, tuple, set, dict)):
        return len(v) > 0
    return True


def _norm_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "sim" if v else "nao"
    return str(v).strip().lower()


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [_norm_str(x) for x in v if _norm_str(x) != ""]
    s = _norm_str(v)
    if s == "":
        return []
    if "," in s:
        return [p.strip() for p in s.split(",") if p.strip()]
    return [s]


def _par(referencia) -> tuple:
    """Extrai ``(min, max)`` de ``[a, b]`` / ``{"min","max"}`` / ``"a,b"``."""
    if isinstance(referencia, dict):
        return referencia.get("min"), referencia.get("max")
    if isinstance(referencia, (list, tuple)):
        return (referencia[0] if len(referencia) > 0 else None,
                referencia[1] if len(referencia) > 1 else None)
    if isinstance(referencia, str) and "," in referencia:
        a, _, b = referencia.partition(",")
        return a.strip(), b.strip()
    return referencia, referencia


def _coerce(v, tipo: str, ctx: dict):
    if tipo == "time":
        return parse_hhmm(v)
    if tipo == "date":
        return _to_epoch(v, _tz(ctx))
    return _to_float(v)


def _to_float(v):
    if v is None or v is True or v is False:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", ".")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_hhmm(v) -> int | None:
    """``"HH:MM"`` → minutos desde a meia-noite. Número puro é tratado como minutos."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    s = str(v).strip()
    if s == "":
        return None
    if ":" in s:
        parts = s.split(":")
        try:
            hh, mm = int(parts[0]), int(parts[1])
        except (TypeError, ValueError):
            return None
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        return hh * 60 + mm
    try:
        return int(float(s))
    except ValueError:
        return None


def _to_epoch(v, tz: float):
    """Data → epoch. Aceita epoch numérico, ``YYYY-MM-DD`` e ISO ``...T...``."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip()
    if s == "":
        return None
    num = _to_float(s)
    if num is not None and len(s) >= 9 and s.replace(".", "").isdigit():
        return num  # já é epoch
    iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return num
    if dt.tzinfo is None:
        # Data/hora "local" da configuração → epoch pelo offset fixo.
        return dt.replace(tzinfo=timezone.utc).timestamp() - tz * 3600
    return dt.timestamp()


def _tz(ctx: dict) -> float:
    try:
        return float(ctx.get(CTX_TZ, -3.0) or 0.0)
    except (TypeError, ValueError):
        return -3.0


def local_dt(epoch: float, tz: float) -> datetime:
    """Instante "local" da configuração (offset fixo) como datetime naïve-em-UTC."""
    return datetime.fromtimestamp(float(epoch) + tz * 3600, timezone.utc)


def epoch_of_local_midnight(epoch: float, tz: float, *, plus_days: int = 0) -> float:
    """Epoch do início do dia local de ``epoch`` (+ ``plus_days`` dias)."""
    d = local_dt(epoch, tz) + timedelta(days=plus_days)
    midnight = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return midnight.timestamp() - tz * 3600


def epoch_of_local_time(epoch: float, tz: float, minutes_of_day: int, *,
                        plus_days: int = 0) -> float:
    """Epoch do instante local ``minutes_of_day`` no dia local de ``epoch`` (+ dias)."""
    return epoch_of_local_midnight(epoch, tz, plus_days=plus_days) + minutes_of_day * 60


# ── Hint de agendamento (porte de calcularProximoAgendamento) ─────────────────

def proximo_instante(arvore, ctx: dict, *, now: float | None = None,
                     fallback_seconds: float = MINUTO) -> float:
    """Quando vale a pena reavaliar esta árvore. É só um **hint**.

    Para cada condição sobre campo ``temporal`` calcula o próximo instante em que ela
    PODERIA passar; devolve o mais CEDO estritamente após ``now``. Sem candidato ⇒
    ``now + fallback_seconds`` (o ciclo reavalia tudo de qualquer forma — D5).
    """
    agora = float(now if now is not None else ctx.get(CTX_NOW) or 0.0)
    tz = _tz(ctx)
    last = ctx.get(CTX_LAST_CLIENT)
    candidatos: list[float] = []
    _coletar(arvore if isinstance(arvore, dict) else {}, ctx, agora, tz, last, candidatos)
    futuros = [c for c in candidatos if c is not None and c > agora]
    if not futuros:
        return agora + fallback_seconds
    return min(min(futuros), agora + 86400 * 30)


def _coletar(no: dict, ctx: dict, agora: float, tz: float, last, out: list) -> None:
    for regra in (no.get("regras") or []):
        if not isinstance(regra, dict):
            continue
        if str(regra.get("tipo") or "").lower() == "grupo":
            _coletar(regra, ctx, agora, tz, last, out)
            continue
        campo_id = str(regra.get("campo") or "")
        if not catalog.is_temporal(campo_id):
            continue
        operador = str(regra.get("operador") or "").lower()
        valor = regra.get("valor")
        if operador == "between":
            valor, _ = _par(valor)
            operador = "gte"
        cand = _candidato(campo_id, operador, valor, ctx, agora, tz, last)
        if cand is not None:
            out.append(cand)


def _candidato(campo_id: str, operador: str, valor, ctx: dict, agora: float,
               tz: float, last) -> float | None:
    if campo_id == "virtual.hora_atual":
        alvo = parse_hhmm(valor)
        if alvo is None:
            return None
        if operador in ("gte", "gt", "eq"):
            hoje = epoch_of_local_time(agora, tz, alvo)
            return hoje if hoje > agora else epoch_of_local_time(agora, tz, alvo, plus_days=1)
        if operador in ("lte", "lt"):
            # A janela reabre na próxima meia-noite local.
            return epoch_of_local_midnight(agora, tz, plus_days=1)
        return None

    if campo_id == "virtual.data_atual":
        alvo = _to_epoch(valor, tz)
        if alvo is None:
            return None
        if operador in ("gte", "gt", "eq"):
            return alvo
        return None

    if last is None:
        return None

    fatores = {
        "modulo.minutos_desde_ultimo_contato": 60.0,
        "modulo.horas_desde_ultimo_contato": 3600.0,
        "modulo.dias_desde_ultimo_contato": 86400.0,
    }
    if campo_id in fatores and operador in ("gte", "gt", "eq"):
        n = _to_float(valor)
        if n is None:
            return None
        return float(last) + n * fatores[campo_id]

    if campo_id == "modulo.dias_calendario_desde_contato" and operador in ("gte", "gt", "eq"):
        n = _to_float(valor)
        if n is None:
            return None
        return epoch_of_local_midnight(float(last), tz, plus_days=int(n))

    return None
