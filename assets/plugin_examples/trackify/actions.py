"""Registro das AÇÕES de botão: o que um código de payload SIGNIFICA.

O módulo Campanhas virou agnóstico — cada botão de resposta rápida de um
template carrega um código livre, e é **aqui** que o código ganha função. Hoje
existe uma só: descadastro de marketing.

Por que um registro EM CÓDIGO e não uma tabela de regras com CRUD (a máquina que
a migration 004 apagou): a função de um código não é dado do operador, é
COMPORTAMENTO — ela decide o que vai ser gravado no CRM do cliente. A tabela
exigia UI de cadastro, migração e validação cruzada, e ainda deixava o operador
adivinhando quais strings existiam. Ação nova aqui = uma dataclass; a aba se
desenha sozinha a partir de ``/consent/status``.

**Fronteira**: a ação declara O QUE gravar (:class:`Plan`); ``consent.py``
decide COMO entregar (tentativas, dedupe por ``msg_id``, mapa de veredito). ``consent_match`` nunca descobre que "ação" existe — ele devolve um
id opaco.

Dependências: ``actions`` → ``_config``, ``consent_match``. Nunca o contrário —
é por isso que o gate ``ready()`` mora aqui e não em ``_config``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import _config, consent_match

# Identidade da ação embutida. Vale como valor da coluna `action` do estado, e
# migrar esta string exigiria migration — trate como identidade, não rótulo.
OPTOUT = "optout"


@dataclass(frozen=True)
class Plan:
    """O que a ação grava no cadastro do CDP.

    ``field_values`` é literalmente o contrato de ``writer.put_contact``:
    ``{slug: valor}``, gravado num PUT só e conferido campo a campo.

    ⚠️ Uma ação que precise fazer algo que **não** seja escrever campo (aplicar
    uma tag, por exemplo) alarga ESTE objeto e o executor genérico de
    ``consent._write_once``. Não dê à :class:`Action` um ``execute`` que fale
    HTTP: ela perderia o dedupe por ``msg_id``, o retry, o mapa de veredito e a
    conferência campo-a-campo do ``client._verify`` — que é a única proteção
    contra o 200-silencioso do CDP (slug desconhecido é ignorado sem erro).
    """

    field_values: dict
    describe: str


@dataclass(frozen=True)
class SettingField:
    """Setting escalar que a ação expõe na sua linha da aba.

    Existe para que uma ação nova não exija UI: a aba renderiza um input por
    descritor destes. O VALOR sai sempre do objeto de settings do cliente — o
    ``/consent/status`` só descreve o campo, nunca é a fonte do valor.
    """

    key: str
    label: str
    help: str = ""
    placeholder: str = ""


@dataclass(frozen=True)
class Action:
    """Uma função que um código de botão pode ativar."""

    id: str
    label: str
    description: str
    codes_setting: str
    codes_label: str
    codes_help: str
    codes_placeholder: str
    # Devolve `None` quando a configuração está incompleta — o motivo sai em
    # `config_errors`. Nunca levanta.
    plan: Callable[[], "Plan | None"] = lambda: None
    config_errors: Callable[[], list] = lambda: []
    settings_fields: tuple = field(default_factory=tuple)

    def codes(self) -> list[str]:
        """Códigos configurados, como digitados. Lista vazia = ação inerte."""
        return consent_match.parse_codes(_config.setting(self.codes_setting, ""))

    def slugs(self) -> tuple[str, ...]:
        """Slugs do CDP que esta ação escreve. Alimenta a guarda cruzada com a
        aba 'Campos do contato' e o veredito de campo da tela."""
        try:
            plano = self.plan()
        except Exception:  # noqa: BLE001 — descritor nunca derruba a tela
            return ()
        return tuple(plano.field_values) if plano else ()

    def errors(self) -> list[str]:
        try:
            return list(self.config_errors() or [])
        except Exception:  # noqa: BLE001
            return []


# ── Ação embutida: descadastro de marketing ──────────────────────────────

def _optout_plan() -> "Plan | None":
    slug = _config.consent_field_slug()
    valor = _config.consent_optout_value()
    if not (slug and valor):
        return None
    return Plan({slug: valor}, f"{slug}={valor!r}")


def _optout_errors() -> list[str]:
    erros = list(consent_match.validate_values(_config.consent_optout_value()))
    if not _config.consent_field_slug():
        erros.append("Sem campo de descadastro configurado: nada seria gravado.")
    if not _config.consent_optout_value():
        erros.append("Sem valor de descadastro configurado: o PUT apagaria o campo "
                     "em vez de bloquear o contato.")
    return erros


OPTOUT_ACTION = Action(
    id=OPTOUT,
    label="Descadastro de marketing",
    description=(
        "Marca o contato no campo de descadastro do Trackify. É esse campo que o "
        "módulo Campanhas lê para montar a lista de disparo — quem está marcado "
        "para de receber."
    ),
    codes_setting="consent_optout_payload",
    codes_label="Códigos que significam descadastro",
    codes_help=(
        "Separados por vírgula. Qualquer clique cujo PAYLOAD seja igual a um "
        "deles descadastra. Maiúscula/minúscula e acento não importam. Ao trocar "
        "o código de um template, ACRESCENTE o novo sem remover o antigo: "
        "campanhas já disparadas continuam recebendo cliques por dias."
    ),
    codes_placeholder="PARAR_PROMOS, PARAR_PROMOS_2",
    plan=_optout_plan,
    config_errors=_optout_errors,
    settings_fields=(
        SettingField(
            "consent_field_slug",
            "Campo de descadastro no Trackify",
            "Tem de ser o MESMO slug configurado no módulo Campanhas "
            "(trackify_campo_optout). Slugs diferentes não quebram nada e fazem o "
            "disparo continuar indo para quem pediu para sair.",
            "optout_marketing",
        ),
        SettingField(
            "consent_optout_value",
            "Valor ao descadastrar",
            "O Campanhas trata vazio, 'nao', 'não', 'n', 'no', 'false' e '0' como "
            "quem CONTINUA recebendo — qualquer outro valor bloqueia.",
            "sim",
        ),
    ),
)


_REGISTRY: dict = {a.id: a for a in (OPTOUT_ACTION,)}


def registered() -> list:
    """As ações do catálogo, na ordem de declaração."""
    return list(_REGISTRY.values())


def get(action_id):
    """A ação de um id, ou ``None`` — inclusive para um id que já existiu e foi
    removido numa versão posterior (linha órfã no estado)."""
    return _REGISTRY.get(str(action_id or ""))


def reserved_slugs() -> dict:
    """``{slug: motivo}`` de TODA ação registrada, para a aba 'Campos do contato'.

    ⚠️ **dict, não set**: ``field_map.validate`` usa o valor como texto do erro
    mostrado ao operador.
    """
    out: dict = {}
    for acao in _REGISTRY.values():
        for slug in acao.slugs():
            out[slug] = (
                f"Este campo é escrito pela ação '{acao.label}' da aba "
                "'Descadastro por botão'. Mapeá-lo aqui ligaria o pull, que traria "
                "o valor antigo do Trackify e poderia DESFAZER o que ela grava."
            )
    return out


@dataclass(frozen=True)
class Snapshot:
    """UMA leitura das settings por clique: índice, conflitos e gate juntos.

    Existe porque ``on_message_received`` roda no evento mais quente do sistema:
    ler as settings duas vezes (uma para o gate, outra para os códigos) dobrava
    o custo do único ponto do handler que toca o banco.
    """

    index: dict
    conflicts: dict
    codes: dict
    enabled: bool
    credential_set: bool

    @property
    def ready(self) -> bool:
        """Ligado, com credencial e com ao menos um código utilizável.

        Código que só existe em conflito NÃO conta: ele não casaria nada, e a
        tela precisa poder dizer "ligado mas inerte" em vez de ficar mudo.
        """
        return bool(self.enabled and self.credential_set and self.index)


def snapshot() -> Snapshot:
    codes = {a.id: a.codes() for a in _REGISTRY.values()}
    index, conflitos = consent_match.code_index(codes)
    return Snapshot(
        index=index,
        conflicts=conflitos,
        codes=codes,
        enabled=bool(_config.setting("consent_enabled", False)),
        credential_set=_config.credential_set(),
    )


def ready() -> bool:
    return snapshot().ready
