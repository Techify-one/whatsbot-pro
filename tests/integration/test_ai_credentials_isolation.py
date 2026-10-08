"""Plano 177 · F3 — entrega e redação de credenciais no runner isolado.

Usa o subprocesso REAL (``python -m agent.tool_runner``) por ``run_isolated_tool``,
contra o Postgres de teste. Prova os cinco casos do plano:

  (a) tool liberada obtém o valor;
  (b) tool não liberada recebe o erro de mensagem fixa;
  (c) tool que DEVOLVE o segredo recebe ``***``;
  (d) tool que lança exceção com o segredo na mensagem recebe ``***``;
  (e) ``caplog`` não contém o valor.

⚠️ Limite documentado, não escondido: base64/URL-encode/fatiar o valor escapa da
redação — ela protege contra o acidente, não contra uma tool escrita para
exfiltrar (o mesmo limite do D9). O último teste do arquivo trava isso de
propósito, para ninguém "descobrir" depois que a redação é à prova de má-fé.
"""

from __future__ import annotations

import base64
import logging

import pytest

from agent import tool_isolation
from agent.tool_isolation import REDACTED, run_isolated_tool, scrub
from db.repositories import credential_repo as repo

CRED = "p177_iso"
TOOL = "p177_iso_tool"
OTHER = "p177_iso_other"
SECRET = "rbk_SEGREDO_p177_93ac"

_HEADER = (
    'SCHEMA = {"type": "function", "function": {"name": "%s", '
    '"description": "d", "parameters": {"type": "object", "properties": {}}}}\n'
)


def _code(name: str, body: str) -> str:
    return (_HEADER % name) + "def execute(ctx, args):\n" + "".join(
        f"    {line}\n" for line in body.strip().splitlines())


@pytest.fixture(autouse=True)
def _vault(_engine_ready):
    # O env.py do Alembic chama logging.config.fileConfig com
    # disable_existing_loggers=True e desliga este logger pelo resto do processo:
    # sem religá-lo, ``caplog`` fica vazio e "o segredo não está no log" passaria
    # sem provar nada.
    previous_disabled = tool_isolation.logger.disabled
    tool_isolation.logger.disabled = False
    repo.delete(CRED)
    repo.upsert_credential(CRED, "teste", [
        {"key": "url", "secret": False, "value": "https://api.exemplo.com"},
        {"key": "chave", "secret": True, "value": SECRET},
    ])
    repo.set_tool_links(CRED, [TOOL])
    yield
    repo.delete(CRED)
    tool_isolation.logger.disabled = previous_disabled


# ── scrub (puro) ─────────────────────────────────────────────────────────────

def test_scrub_replaces_every_occurrence():
    assert scrub(f"a {SECRET} b {SECRET}", [SECRET]) == f"a {REDACTED} b {REDACTED}"


def test_scrub_accepts_a_five_digit_secret_with_its_documented_side_effect():
    # D13: sem tamanho mínimo. O efeito colateral é aceito e declarado: um ID
    # que CONTÉM o valor curto volta mascarado.
    assert scrub("senha=48213", ["48213"]) == f"senha={REDACTED}"
    assert scrub("id=4821399", ["48213"]) == f"id={REDACTED}99"


def test_scrub_ignores_empty_secret():
    # "".replace("", "***") espalharia *** entre todos os caracteres.
    assert scrub("abc", [""]) == "abc"
    assert scrub("abc", ["", "b"]) == f"a{REDACTED}c"


def test_scrub_longest_first_leaves_no_tail():
    assert scrub("x abcd y", ["abc", "abcd"]) == f"x {REDACTED} y"
    assert scrub("x abcd y", ["abcd", "abc"]) == f"x {REDACTED} y"


def test_scrub_passes_through_none_and_no_secrets():
    assert scrub(None, [SECRET]) is None
    assert scrub("texto", []) == "texto"
    assert scrub("", [SECRET]) == ""


# ── (a) tool liberada obtém o valor ──────────────────────────────────────────

def test_linked_tool_reads_the_credential():
    code = _code(TOOL, '''
c = ctx.credential("p177_iso")
return "url=%s;tem_chave=%s" % (c["url"], bool(c["chave"]))
''')
    out = run_isolated_tool(TOOL, code, None, {})
    assert out == "url=https://api.exemplo.com;tem_chave=True"


def test_credential_value_really_crosses_the_process_boundary():
    # Prova que a tool recebeu o valor INTEIRO (não a versão redigida): ela o
    # compara por dentro e devolve só o veredito.
    code = _code(TOOL, f'''
return "igual" if ctx.credential("p177_iso")["chave"] == {SECRET!r} else "diferente"
''')
    assert run_isolated_tool(TOOL, code, None, {}) == "igual"


# ── (b) tool não liberada ────────────────────────────────────────────────────

def test_unlinked_tool_gets_the_fixed_error_message():
    code = _code(OTHER, '''
return ctx.credential("p177_iso")["chave"]
''')
    out = run_isolated_tool(OTHER, code, None, {})
    assert out == f"[tool '{OTHER}' erro: LookupError: credencial não disponível para esta tool]"
    assert SECRET not in out


def test_missing_and_unlinked_credentials_are_indistinguishable():
    unlinked = run_isolated_tool(OTHER, _code(OTHER, 'return ctx.credential("p177_iso")["chave"]'), None, {})
    missing = run_isolated_tool(OTHER, _code(OTHER, 'return ctx.credential("nao_existe")["chave"]'), None, {})
    assert unlinked == missing


def test_unlinked_tool_gets_no_credentials_in_its_payload():
    # A tool tenta achar o valor por qualquer via do ctx: nada.
    code = _code(OTHER, '''
return "%r|%r|%r" % (ctx.credentials, ctx.get("credentials"), ctx.contact)
''')
    out = run_isolated_tool(OTHER, code, None, {})
    assert SECRET not in out and "api.exemplo.com" not in out


def test_unlinking_takes_effect_on_the_very_next_call():
    code = _code(TOOL, 'return ctx.credential("p177_iso")["url"]')
    assert run_isolated_tool(TOOL, code, None, {}) == "https://api.exemplo.com"
    repo.set_tool_links(CRED, [])
    assert "credencial não disponível" in run_isolated_tool(TOOL, code, None, {})


# ── (c) devolve o segredo ────────────────────────────────────────────────────

def test_result_that_echoes_the_secret_is_redacted():
    code = _code(TOOL, 'return "chave=" + ctx.credential("p177_iso")["chave"]')
    out = run_isolated_tool(TOOL, code, None, {})
    assert out == f"chave={REDACTED}"
    assert SECRET not in out


def test_non_secret_field_is_not_redacted():
    code = _code(TOOL, 'return ctx.credential("p177_iso")["url"]')
    assert run_isolated_tool(TOOL, code, None, {}) == "https://api.exemplo.com"


# ── (d) exceção com o segredo ────────────────────────────────────────────────

def test_exception_message_carrying_the_secret_is_redacted():
    code = _code(TOOL, '''
c = ctx.credential("p177_iso")
raise RuntimeError("falhou em " + c["url"] + "?key=" + c["chave"])
''')
    out = run_isolated_tool(TOOL, code, None, {})
    assert SECRET not in out
    assert f"?key={REDACTED}" in out


def test_runner_level_failure_with_the_secret_on_stderr_is_redacted():
    # Falha ao importar: o motivo vai por stderr, código de saída 1.
    code = f'SECRET = {SECRET!r}\nraise ValueError("import falhou: " + SECRET)\n'
    out = run_isolated_tool(TOOL, code, None, {})
    # Este valor veio do CÓDIGO da tool, não do cofre — ainda assim, como o
    # cofre da tool tem o mesmo valor, a redação o cobre.
    assert SECRET not in out
    assert "falhou" in out


# ── (e) log limpo ────────────────────────────────────────────────────────────

def test_logs_never_contain_the_secret(caplog):
    caplog.set_level(logging.DEBUG)
    run_isolated_tool(TOOL, _code(TOOL, '''
c = ctx.credential("p177_iso")
print("debug " + c["chave"])
raise RuntimeError("erro " + c["chave"])
'''), None, {})
    run_isolated_tool(TOOL, f'raise ValueError({SECRET!r})\n', None, {})
    # Anti-vácuo: o teste só vale se o runner realmente LOGOU algo.
    assert any("AI tool" in r.getMessage() for r in caplog.records), caplog.text
    assert SECRET not in caplog.text
    assert SECRET not in "\n".join(r.getMessage() for r in caplog.records)


def test_tool_print_of_the_secret_goes_to_stderr_and_stays_out_of_the_result(caplog):
    caplog.set_level(logging.DEBUG)
    out = run_isolated_tool(TOOL, _code(TOOL, '''
print("vazou " + ctx.credential("p177_iso")["chave"])
return "ok"
'''), None, {})
    assert out == "ok"
    assert SECRET not in caplog.text


# ── describe / vault ilegível / sem credencial ───────────────────────────────

def test_describe_mode_never_receives_credentials(monkeypatch):
    sent = {}
    real = tool_isolation.run_oneshot

    def spy(cmd, *, stdin_data, **kw):
        sent["stdin"] = stdin_data
        return real(cmd, stdin_data=stdin_data, **kw)

    monkeypatch.setattr(tool_isolation, "run_oneshot", spy)
    schemas = tool_isolation.describe_isolated_tool(TOOL, _code(TOOL, "return None"))
    assert schemas[0]["function"]["name"] == TOOL
    assert b"credentials" not in sent["stdin"]
    assert SECRET.encode() not in sent["stdin"]


def test_payload_is_unchanged_for_a_tool_with_no_credentials(monkeypatch):
    sent = {}
    real = tool_isolation.run_oneshot

    def spy(cmd, *, stdin_data, **kw):
        sent["stdin"] = stdin_data
        return real(cmd, stdin_data=stdin_data, **kw)

    monkeypatch.setattr(tool_isolation, "run_oneshot", spy)
    assert run_isolated_tool(OTHER, _code(OTHER, 'return "ok"'), None, {}) == "ok"
    assert b"credentials" not in sent["stdin"]


def test_unreadable_vault_is_fail_closed_and_logs_no_value(monkeypatch, caplog):
    def boom(_name):
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(repo, "for_tool", boom)
    caplog.set_level(logging.WARNING)
    out = run_isolated_tool(TOOL, _code(TOOL, 'return ctx.credential("p177_iso")["chave"]'), None, {})
    assert "credencial não disponível" in out          # roda SEM credencial e falha claro
    assert "could not read credentials" in caplog.text
    assert SECRET not in caplog.text + out


# ── limite declarado ─────────────────────────────────────────────────────────

def test_known_limit_an_encoded_secret_escapes_redaction():
    """Documenta o limite (D9): base64 do valor NÃO é redigido.

    Se este teste algum dia falhar porque a redação passou a pegar base64, ótimo
    — atualize o teste e o texto da tela. O que não se pode é a tela prometer
    mais do que a redação entrega.
    """
    code = _code(TOOL, '''
import base64
return base64.b64encode(ctx.credential("p177_iso")["chave"].encode()).decode()
''')
    out = run_isolated_tool(TOOL, code, None, {})
    assert out == base64.b64encode(SECRET.encode()).decode()
    assert out != REDACTED
