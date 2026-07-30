"""Motor de AGRUPAMENTO do Kanban de protocolos (backend).

Porta fiel do ``buildGrouping``/``columnIdOf`` que vivia SÓ no frontend
(``static/protocolos_tab.js``). O servidor passa a classificar cada protocolo numa
coluna, o que permite **paginação por coluna** (ver ``kanban_index.py``) em vez de
carregar a coleção inteira no cliente.

Genérico por construção: um único ``Grouping.column_id_of`` cobre TODOS os
``group_by`` (status / atendente / data / pfield). Agrupamento novo = mais um ramo
aqui — índice, rotas e frontend não mudam.

Os ids de coluna são os MESMOS do frontend (``aberto``/``fechado``, ``u:<id>``,
``d:``/``w:``/``m:``, ``o:<valor>``, ``__none__``/``__nodate__``/``__outofrange__``),
para que a ordem de colunas salva por view (``column_order``) continue valendo.

Datas usam um **timezone FIXO do servidor** (config ``plugin.protocolos.kanban_timezone``,
default ``America/Sao_Paulo``) — no frontend era o fuso do navegador. ``now_epoch`` é
injetável para testes determinísticos.

Módulo PURO: não importa ``logic`` (a resolução de def de campo entra por injeção),
então dá para testá-lo sem banco.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TZ_CONFIG_KEY = "plugin.protocolos.kanban_timezone"
DEFAULT_TZ = "America/Sao_Paulo"

# Colunas especiais — sempre ao FIM da lista (mesma regra do frontend).
COL_NONE = "__none__"
COL_NODATE = "__nodate__"
COL_OUTOFRANGE = "__outofrange__"

_MONTH_ABBR_PT = ["jan", "fev", "mar", "abr", "mai", "jun",
                  "jul", "ago", "set", "out", "nov", "dez"]


def resolve_tz(name: str | None = None) -> ZoneInfo:
    """ZoneInfo do timezone configurado. Fail-open: nunca lança (cai no default e, em
    último caso, UTC) — um fuso mal digitado não pode derrubar o Kanban."""
    for cand in (name, DEFAULT_TZ):
        if not cand:
            continue
        try:
            return ZoneInfo(str(cand))
        except Exception:
            logger.warning("protocolos: timezone inválido %r — usando o default.", cand)
    return ZoneInfo("UTC")


# ── Helpers de data (todos no timezone resolvido) ─────────────────────────────

def _local(epoch, tz) -> datetime:
    return datetime.fromtimestamp(float(epoch or 0), tz)


def _day_start(d: datetime, tz) -> float:
    return datetime(d.year, d.month, d.day, tzinfo=tz).timestamp()


def ymd_start_epoch(s, tz) -> float | None:
    """``'YYYY-MM-DD'`` → epoch da meia-noite local. ``None`` se ausente/inválida."""
    try:
        y, m, d = (int(x) for x in str(s).split("-"))
        return datetime(y, m, d, tzinfo=tz).timestamp()
    except Exception:
        return None


def ymd_end_epoch(s, tz) -> float | None:
    """``'YYYY-MM-DD'`` → epoch de 23:59:59 local (janela inclusiva por dia)."""
    try:
        y, m, d = (int(x) for x in str(s).split("-"))
        return datetime(y, m, d, 23, 59, 59, tzinfo=tz).timestamp()
    except Exception:
        return None


def _ymd_label(key: str) -> str:
    y, m, d = key.split("-")
    return f"{d}/{m}/{y}"


def _month_label(key: str) -> str:
    y, m = key.split("-")
    return f"{_MONTH_ABBR_PT[int(m) - 1]}. de {y}"


def _week_label(key: str) -> str:
    return f"Semana de {_ymd_label(key)}"


def _grain_keyer(grain: str, tz):
    """(keyer(epoch)->chave, prefixo do id, labeler(chave)->rótulo) por granularidade."""
    if grain == "mes":
        return (lambda t: _local(t, tz).strftime("%Y-%m")), "m:", _month_label
    if grain == "semana":
        def _week(t):
            d = _local(t, tz)
            return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")  # segunda-feira
        return _week, "w:", _week_label
    return (lambda t: _local(t, tz).strftime("%Y-%m-%d")), "d:", _ymd_label


# ── Grouping ──────────────────────────────────────────────────────────────────

class Grouping:
    """Classificador + colunas de UMA visualização.

    ``needs`` diz quais anexações de hidratação o índice precisa rodar para poder
    classificar (ver ``kanban_index._enrich``) — é o que mantém os modos baratos
    (status/atendente/data) sem NENHUMA anexação.
    """

    def __init__(self, *, group_by, column_id_of, static_columns=None,
                 label_for=None, special=None, needs=(), read_only=False,
                 unavailable=False):
        self.group_by = group_by
        self.column_id_of = column_id_of
        self._static = static_columns
        self._label_for = label_for or (lambda cid: cid)
        self.special = special          # coluna especial, vai sempre ao FIM
        self.needs = set(needs)
        self.read_only = read_only
        self.unavailable = unavailable

    @property
    def is_dynamic(self) -> bool:
        """True quando as colunas derivam dos dados (modos de data por bucket)."""
        return self._static is None

    def static_columns(self):
        return [dict(c) for c in self._static] if self._static is not None else None

    def label_for(self, col_id: str) -> str:
        """Rótulo de uma coluna. Serve também para ids classificados FORA do conjunto
        estático (ex.: protocolo de um usuário inativo, que não vira coluna) — o índice
        acrescenta essas colunas ao fim em vez de sumir com os cards."""
        for c in (self._static or []):
            if c["id"] == col_id:
                return c["label"]
        return self._label_for(col_id)

    def build_dynamic_columns(self, seen_ids) -> list[dict]:
        """Colunas dos modos dinâmicos: chaves distintas em ordem DECRESCENTE de string
        (= cronológico desc, como o ``.sort().reverse()`` do frontend) + a especial ao fim."""
        seen = set(seen_ids)
        keys = sorted((c for c in seen if c != self.special), reverse=True)
        cols = [{"id": k, "label": self._label_for(k)} for k in keys]
        if self.special and self.special in seen:
            cols.append({"id": self.special, "label": self._label_for(self.special)})
        if not cols and self.special:  # nada agrupável → só a especial (igual ao frontend)
            cols = [{"id": self.special, "label": self._label_for(self.special)}]
        return cols


def _unavailable(label: str) -> Grouping:
    """Agrupamento impossível (campo removido / modo legado): 1 coluna, só-leitura."""
    return Grouping(group_by="unavailable", column_id_of=lambda row: COL_NONE,
                    static_columns=[{"id": COL_NONE, "label": label}],
                    read_only=True, unavailable=True)


def _grouping_status() -> Grouping:
    return Grouping(
        group_by="status",
        column_id_of=lambda row: "fechado" if row.get("status") == "fechado" else "aberto",
        static_columns=[{"id": "aberto", "label": "Aberto"},
                        {"id": "fechado", "label": "Fechado"}])


def _grouping_atendente(users) -> Grouping:
    """Colunas = "Não atribuído" + UMA por usuário ativo (não só os assignees presentes).

    Classifica pelo atendente EFETIVO: o definitivo (``assignee_user_id``, salvo no
    formulário de Resolver/Finalizar) e, na falta dele, o PROVISÓRIO (o dono da conversa
    no core). Lê as colunas CRUAS de propósito — este módulo é puro e recebe dicts
    montados à mão nos testes. Ids de coluna ficam inalterados (``u:<id>``/``__none__``),
    então o ``column_order`` das views salvas continua válido."""
    cols = [{"id": COL_NONE, "label": "Não atribuído"}]
    for u in (users or []):
        uid = u.get("id")
        cols.append({"id": f"u:{uid}", "label": u.get("name") or f"Usuário #{uid}"})

    def _cid(row):
        a = row.get("assignee_user_id")
        if a is None:
            a = row.get("provisional_assignee_user_id")
        return f"u:{a}" if a is not None else COL_NONE

    def _label_for(col_id):   # usuário fora da lista (inativo/removido) ainda ganha coluna
        return f"Usuário #{col_id[2:]}" if col_id.startswith("u:") else col_id

    return Grouping(group_by="atendente", column_id_of=_cid, static_columns=cols,
                    label_for=_label_for)


def _grouping_data(view, now_epoch: float, tz) -> Grouping:
    """Agrupamento por data — SEMPRE só-leitura (não há drag no modo data)."""
    mode = view.get("group_date_mode") or "faixas"

    if mode == "faixas":
        now_dt = _local(now_epoch, tz)
        s_today = _day_start(now_dt, tz)
        s_yest = s_today - 86400
        s_week = s_today - 6 * 86400
        s_month = datetime(now_dt.year, now_dt.month, 1, tzinfo=tz).timestamp()

        def _cid(row):
            t = float(row.get("opened_at") or 0)
            if t >= s_today:
                return "today"
            if t >= s_yest:
                return "yesterday"
            if t >= s_week:
                return "week"
            if t >= s_month:
                return "month"
            return "older"

        return Grouping(group_by="data", column_id_of=_cid, read_only=True,
                        static_columns=[{"id": "today", "label": "Hoje"},
                                        {"id": "yesterday", "label": "Ontem"},
                                        {"id": "week", "label": "Últimos 7 dias"},
                                        {"id": "month", "label": "Este mês"},
                                        {"id": "older", "label": "Mais antigos"}])

    if mode == "personalizado":
        grain = view.get("group_date_grain") or "dia"
        from_e = ymd_start_epoch(view.get("group_date_from"), tz)
        to_e = ymd_end_epoch(view.get("group_date_to"), tz)
        special = COL_OUTOFRANGE
    else:  # 'dia' | 'semana' | 'mes' — bucketiza tudo, sem janela
        grain = mode
        from_e = to_e = None
        special = COL_NODATE

    keyer, prefix, labeler = _grain_keyer(grain, tz)

    def _cid(row):
        raw = row.get("opened_at")
        t = float(raw or 0)
        if special == COL_OUTOFRANGE:
            in_win = (from_e is None or t >= from_e) and (to_e is None or t <= to_e)
            if not in_win:
                return COL_OUTOFRANGE
            return (prefix + keyer(t)) if raw else COL_OUTOFRANGE
        return (prefix + keyer(t)) if raw else COL_NODATE

    def _label_for(col_id):
        if col_id == COL_NODATE:
            return "Sem data"
        if col_id == COL_OUTOFRANGE:
            return "Fora do período"
        return labeler(col_id[2:])   # remove o prefixo de 2 chars

    return Grouping(group_by="data", column_id_of=_cid, static_columns=None,
                    label_for=_label_for, special=special, read_only=True)


def _grouping_pfield(view, option_def) -> Grouping:
    """Agrupa por um campo de OPÇÃO (escopo protocolo ou atendimento)."""
    scope = view.get("group_field_scope") or "protocolo"
    key = view.get("group_attr_key") or ""
    d = option_def(scope, key) if option_def else None
    if not d:
        return _unavailable("Campo indisponível")

    cols = [{"id": COL_NONE, "label": "Sem valor"}]
    cols += [{"id": f"o:{o}", "label": str(o)} for o in (d.get("options") or [])]
    # protocolo → ``fields`` (extras do protocolo); atendimento → ``atendimento_fields``.
    src_key = "fields" if scope == "protocolo" else "atendimento_fields"

    def _cid(row):
        v = (row.get(src_key) or {}).get(key)
        if isinstance(v, list):        # checkboxes de valor único guardam lista de 1
            v = v[0] if v else ""
        return f"o:{v}" if (v is not None and v != "") else COL_NONE

    def _label_for(col_id):   # valor fora das opções atuais (opção removida) vira coluna
        return col_id[2:] if col_id.startswith("o:") else col_id

    needs = {"protocolo_extras"} if scope == "protocolo" else {"atendimento_fields"}
    return Grouping(group_by="pfield", column_id_of=_cid, static_columns=cols,
                    label_for=_label_for, needs=needs)


def build_grouping(view, *, users=None, option_def=None, now_epoch=None, tz=None) -> Grouping:
    """Monta o ``Grouping`` de uma visualização.

    ``users``      = usuários ativos ``[{'id','name'}]`` (colunas do modo atendente).
    ``option_def`` = ``callable(scope, key) -> def | None`` de campo de OPÇÃO (normalmente
                     ``logic._option_field_def``), injetado p/ manter este módulo puro.
    ``now_epoch``  = "agora" (injetável em teste); ``tz`` = ZoneInfo (default: config).
    """
    view = view or {}
    tz = tz if tz is not None else resolve_tz()
    now_epoch = float(now_epoch if now_epoch is not None else time.time())
    gb = view.get("group_by") or "status"

    if gb == "atendente":
        return _grouping_atendente(users)
    if gb == "data":
        return _grouping_data(view, now_epoch, tz)
    if gb == "pfield":
        return _grouping_pfield(view, option_def)
    if gb == "attr":            # legado descontinuado (mesmo tratamento do frontend)
        return _unavailable("Agrupamento indisponível")
    return _grouping_status()
