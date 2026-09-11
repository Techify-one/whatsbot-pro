"""Tools de PLUGIN como rows editáveis em ``ai_tools`` (``kind='plugin'``).

Irmão de :mod:`agent.ai_builtin_tools`, e genérico: o core não conhece nenhum
plugin por nome. Toda tool contribuída por ``entry.tools`` ganha uma row semeada
a partir da fonte em disco do plugin, e com isso a tela unificada de Tools passa
a mostrar versão, status, **Editar código**, **Histórico** e **Excluir** — do
mesmo jeito que já mostra para as quatro tools core.

────────────────────────────────────────────────────────────────────────────
MODELO DE SEGURANÇA (fail-closed — uma tool de plugin nunca some sozinha)
────────────────────────────────────────────────────────────────────────────
  • A baseline confiável é o ``CORE_TOOLS`` do disco, que o loader registra no
    boot como sempre fez. Ela não depende do banco.
  • Semear é idempotente e nunca sobrescreve edição do operador.
  • Row NÃO EDITADA (``version <= 1``) mantém a registração do disco; o código
    do banco NÃO é executado — ele é só o que a tela mostra.
  • Row EDITADA (``version > 1``) tem o código do banco compilado e executado
    IN-PROCESS, sobrepondo a baseline. ⚠️ É código arbitrário com privilégio de
    host — mas só depois de um humano com ``agent.tools.manage`` ter editado.
    Não é uma capacidade nova: o ``tools.py`` do plugin já roda in-process.
  • Edição que não compila ou não expõe o contrato ⇒ a baseline FICA e a row vai
    a ``install_status='failed'`` com o motivo visível na tela.
  • Row desabilitada ⇒ a tool é desregistrada.

Por isto NÃO passa pelo kill-switch ``ai_tools_code_enabled``: aquele guarda o
caminho ISOLADO de código autoral (e as instalações de pip que ele dispara). Uma
tool de plugin editada sobrepõe um módulo que veio num ``.zip`` instalado
deliberadamente, e o switch está OFF por padrão — gatear aqui deixaria os botões
Editar/Histórico como enfeite em praticamente toda instalação.

────────────────────────────────────────────────────────────────────────────
O MÓDULO É A UNIDADE DE EDIÇÃO; A ROW É A UNIDADE DE EXIBIÇÃO
────────────────────────────────────────────────────────────────────────────
Um ``tools.py`` costuma declarar VÁRIAS tools (``CORE_TOOLS``), e a identidade de
uma row é o NOME da tool. Logo N tools de um módulo viram N rows com o MESMO
código. Salvar uma delas propaga para as irmãs (``sibling_names`` + o fan-out no
``PUT``) — sem isso o boot seguinte teria a row A executando o módulo do banco e
as irmãs executando o do disco: dois objetos de módulo, dois conjuntos de estado
de módulo, e uma constante editada valendo para uma tool e não para as outras.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agent.ai_builtin_tools import deleted_tools
from agent.ai_tool_compile import compile_tool_namespace, extract_tool
from db.repositories import tool_repo

logger = logging.getLogger(__name__)

KIND = "plugin"


# ── provenance ───────────────────────────────────────────────────────────────
def _groups(registry) -> list[dict]:
    """Um grupo por plugin com tools: nomes, fonte do disco e pacote Python.

    Um grupo é o que se compila de uma vez. Ler a fonte com ``Path.read_text``
    (e não ``inspect.getsource``) porque o módulo do plugin foi importado por
    ``spec_from_file_location`` e o arquivo no disco é a resposta direta.
    """
    out: list[dict] = []
    for loaded in getattr(registry, "loaded", {}).values():
        if not loaded.tools or not loaded.tools_module:
            continue
        module_name, module_file = loaded.tools_module
        try:
            source = Path(module_file).read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("plugin '%s': não deu para ler %s (%s)",
                           loaded.id, module_file, e)
            continue
        entries = []
        for schema, _executor in loaded.tools:
            name = ((schema or {}).get("function") or {}).get("name")
            if name:
                entries.append((str(name), (schema.get("function") or {}).get(
                    "description", "")))
        if entries:
            out.append({
                "plugin_id": loaded.id,
                "package": loaded.package_name,
                "module_name": module_name,
                "module_file": module_file,
                "source": source,
                "entries": entries,
            })
    return out


def sibling_names(name: str) -> list[str]:
    """Os outros nomes que dividem o MESMO módulo desta tool de plugin.

    Resolvido pelo banco (``plugin_id`` + código idêntico), não pelo registry:
    a rota de save precisa da resposta sem depender de um plugin carregado.
    """
    row = tool_repo.get(name)
    if not row or row.get("kind") != KIND or not row.get("plugin_id"):
        return []
    code = row.get("code") or ""
    return [
        r["name"] for r in tool_repo.list_for_plugin(row["plugin_id"])
        if r["name"] != name and (r.get("code") or "") == code
    ]


# ── quem era o dono de uma tool tombada ──────────────────────────────────────
#
# O tombstone em si é uma lista de NOMES, compartilhada com as builtins. Isso
# basta para não ressuscitar, mas não para RESSUSCITAR DE PROPÓSITO: quando a
# row ``ai_tools`` é apagada no delete, some junto o ``plugin_id`` — e a
# desinstalação do plugin não teria como saber que aquele nome era dele. O
# efeito seria um plugin que volta permanentemente incompleto, sem nenhuma UI
# para recuperar a tool. Por isso o dono é anotado à parte, no delete.
OWNERS_CONFIG_KEY = "deleted_plugin_tool_owners"


def _owners() -> dict[str, str]:
    try:
        from db.repositories import config_repo
        raw = config_repo.get(OWNERS_CONFIG_KEY, None)
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
    except Exception as e:  # noqa: BLE001 — leitura best-effort
        logger.warning("%s: leitura falhou (%s)", OWNERS_CONFIG_KEY, e)
    return {}


def remember_tombstone_owner(name: str, plugin_id: str) -> None:
    """Anota que a tool tombada ``name`` pertencia a ``plugin_id``."""
    if not name or not plugin_id:
        return
    from db.repositories import config_repo
    owners = _owners()
    if owners.get(name) == plugin_id:
        return
    owners[name] = plugin_id
    config_repo.set(OWNERS_CONFIG_KEY, owners)


def tombstoned_names_for(plugin_id: str) -> list[str]:
    """Os nomes tombados que pertenciam a este plugin."""
    return sorted(n for n, pid in _owners().items() if pid == plugin_id)


def forget_tombstone_owners(names) -> int:
    """Esquece o dono de ``names`` (a tool voltou, ou o plugin sumiu de vez)."""
    wanted = {str(n) for n in (names or ())}
    if not wanted:
        return 0
    from db.repositories import config_repo
    owners = _owners()
    restantes = {n: pid for n, pid in owners.items() if n not in wanted}
    if len(restantes) == len(owners):
        return 0
    config_repo.set(OWNERS_CONFIG_KEY, restantes)
    return len(owners) - len(restantes)


# ── seed ─────────────────────────────────────────────────────────────────────
def seed_plugin_tools(registry) -> None:
    """Cria/refresca as rows das tools de plugin a partir da fonte em disco.

    Row ausente ⇒ INSERT. Row não editada com código diferente do disco ⇒
    refresh no lugar (``sync_source``), porque um plugin é atualizado por
    ``.zip`` e a row v1 semeada na versão anterior mostraria código velho no
    editor e no Histórico — o editor mentiria. Row editada ⇒ intocada, com
    WARNING dizendo que a atualização do plugin não foi aplicada ao código dela.
    """
    tombstoned = deleted_tools()
    for group in _groups(registry):
        for name, description in group["entries"]:
            if name in tombstoned:
                continue
            try:
                existing = tool_repo.get(name)
                if existing is None:
                    tool_repo.save(
                        name,
                        description=description,
                        code=group["source"],
                        dependencies=[],
                        enabled=_seed_enabled(name),
                        kind=KIND,
                        plugin_id=group["plugin_id"],
                    )
                    tool_repo.set_status(name, "ok", None)
                    logger.info("Seeded plugin tool '%s' (%s) into ai_tools",
                                name, group["plugin_id"])
                    continue
                if existing.get("kind") not in (KIND, None, "code"):
                    # Nome de tool de plugin colidindo com uma builtin: o
                    # registry já resolveu a colisão a favor do core, e a row
                    # é do core. Não sequestrar.
                    continue
                if int(existing.get("version", 1)) > 1:
                    if (existing.get("code") or "") != group["source"]:
                        logger.warning(
                            "plugin tool '%s' está editada (v%s); a atualização do "
                            "plugin '%s' não foi aplicada ao código no banco",
                            name, existing.get("version"), group["plugin_id"])
                    continue
                if tool_repo.sync_source(
                    name,
                    description=description,
                    code=group["source"],
                    kind=KIND,
                    plugin_id=group["plugin_id"],
                ):
                    logger.info("plugin tool '%s': código do banco realinhado com "
                                "o disco (%s)", name, group["plugin_id"])
            except Exception as e:  # noqa: BLE001 — seeding é best-effort
                logger.warning("Could not seed plugin tool '%s': %s", name, e)


def _seed_enabled(name: str) -> bool:
    """Estado inicial da row nova: espelha o override existente, se houver.

    Semear ``True`` por cima de um ``tool_overrides.enabled = 0` recriaria, do
    outro lado, exatamente a divergência de gates que ``default_override_enabled``
    existe para evitar: o operador desligou a tool, e a row nova diria o oposto.
    """
    try:
        from db.repositories import tool_override_repo
        row = tool_override_repo.get(name)
        if row is not None:
            return bool(row.get("enabled", 1))
    except Exception:  # noqa: BLE001 — na dúvida, nasce ligada (é funcionalidade)
        pass
    return True


# ── reconcile ────────────────────────────────────────────────────────────────
def register_plugin_tool_overrides(registry, handler) -> None:
    """Reconcilia a registração de cada tool de plugin com a row dela.

    Roda no boot DEPOIS de o loader ter registrado a baseline do disco e ANTES
    de ``refresh_tool_overrides`` — que é quem reconstrói a lista efetiva de
    schemas mandada ao LLM. Chamar isto depois dele faria a edição nunca chegar
    ao modelo, calada.
    """
    tombstoned = deleted_tools()
    for group in _groups(registry):
        # Um módulo, uma compilação: as N tools de um ``tools.py`` resolvem do
        # MESMO namespace. Chave é o código, porque duas rows do grupo podem
        # divergir (uma editada, outra não).
        compiled: dict[str, dict] = {}
        for name, _description in group["entries"]:
            if name in tombstoned:
                handler.unregister_tool(name)
                continue
            try:
                row = tool_repo.get(name)
            except Exception as e:  # noqa: BLE001
                logger.warning("plugin tool '%s': cannot read row (%s)", name, e)
                continue
            if row is None or row.get("kind") != KIND:
                # Seed não rodou, ou a row é de outro dono — a baseline fica.
                continue

            if not row.get("enabled", True):
                handler.unregister_tool(name)
                _safe_status(name, "ok", None)
                continue

            if int(row.get("version", 1)) <= 1:
                # Não editada → mantém a registração confiável do disco.
                _safe_status(name, "ok", None)
                continue

            code = row.get("code") or ""
            try:
                if code not in compiled:
                    compiled[code] = compile_tool_namespace(
                        code,
                        package=group["package"],
                        module_name=group["module_name"],
                        file=group["module_file"],
                        label=name,
                    )
                schema, execute = extract_tool(compiled[code], name)
                # plugin_id explícito: sem ele a tool perde o ToolContext.plugin_id
                # e qualquer acesso a ctx.plugin_db morre.
                handler.override_tool(schema, execute, plugin_id=group["plugin_id"])
                _safe_status(name, "ok", None)
                logger.info("Plugin tool '%s' overridden by edited DB code (v%s)",
                            name, row.get("version"))
            except Exception as e:  # noqa: BLE001 — mantém a baseline, marca failed
                logger.error(
                    "Plugin tool '%s' edit failed (%s); keeping on-disk version.",
                    name, e,
                )
                _safe_status(name, "failed", str(e))


def mark_orphan_rows(registry) -> int:
    """Marca (não apaga) rows cujo plugin não está mais carregado.

    Apagar seria destruir o Python editado pelo operador por causa de um toggle
    ou de uma pasta removida à mão. A row fica visível, em ``failed``, com um
    motivo legível — e o operador decide se aperta Excluir.
    """
    vivos = {
        loaded.id for loaded in getattr(registry, "loaded", {}).values()
    }
    marcadas = 0
    try:
        rows = [r for r in tool_repo.list_all() if r.get("kind") == KIND]
    except Exception as e:  # noqa: BLE001
        logger.warning("mark_orphan_rows: leitura falhou (%s)", e)
        return 0
    for row in rows:
        pid = row.get("plugin_id")
        if pid and pid not in vivos and row.get("install_status") != "failed":
            _safe_status(row["name"], "failed",
                         f"plugin '{pid}' não está carregado")
            marcadas += 1
    return marcadas


def _safe_status(name: str, status: str, error: str | None) -> None:
    try:
        tool_repo.set_status(name, status, error)
    except Exception as e:  # noqa: BLE001
        logger.warning("plugin tool '%s': set_status failed (%s)", name, e)
