"""Re-geração corretiva do script guard no motor AGNO (plano 167 · Fase 2).

Duas frentes neste arquivo:

* **Caracterização** (rodada e verde ANTES de editar ``agent/agno_engine.py``,
  disciplina do repo para fluxo crítico): trava que (i) uma resposta limpa não
  gera chamada extra nenhuma e (ii) o *forced follow-up* que já existe
  continua substituindo a reply e somando usage — a trava nova não pode
  interferir nele. Os mesmos dois testes seguem valendo DEPOIS da edição (são
  também o guard de não-regressão).
* **Comportamento da trava** — depois que o motor ganha o bloco de
  re-geração: detecção por letra não-latina, uma re-geração sem tools, e
  fallback para a resposta original quando a 2ª tentativa também reprova.

Seam: ``agno_engine.build_runner`` (a chamada primária) e
``agno_engine._build_followup_agent`` (usada tanto pelo *forced follow-up*
quanto pela re-geração do script guard — ambas tools-less por design, ver
CLAUDE.md/docs/IA.md) são monkeypatchadas com fakes mínimos. Quando um teste
precisa que uma tool REALMENTE execute (para popular ``executed`` e disparar
``_needs_forced_followup``), o fake runner invoca o entrypoint real que
``agno_engine.build_functions`` produziu — mesmo seam de
``tests/integration/characterization/test_agent_turn_characterization.py``.

Ambos os caminhos públicos (``run_async``/``run_sync``) são exercitados via a
fixture ``is_async`` — são implementações irmãs, sem chamador comum.

    venv/bin/python -m pytest tests/core/test_agno_script_retry.py -q
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agno.models.message import Message

from agent import agno_engine
from server.helpers import parse_split_reply


# ── fakes ────────────────────────────────────────────────────────────────

class _FakeHandler:
    """``.api_key``/``._dispatch_tool`` só importam se build_model ou uma tool
    real rodassem — aqui build_runner/._build_followup_agent estão sempre
    monkeypatchados, e ``_dispatch_tool`` só é lido quando um teste liga uma
    tool de verdade."""

    def __init__(self):
        self.api_key = "fake-key"

    def _dispatch_tool(self, contact, name, args):
        return "ok, feito"


class _FakeRunOutput:
    def __init__(self, messages, in_tokens=10, out_tokens=5):
        self.messages = messages
        self.content = None  # força _extract_reply a usar messages
        self.metrics = SimpleNamespace(
            input_tokens=in_tokens, output_tokens=out_tokens,
            total_tokens=in_tokens + out_tokens)


def _clean_output(text, in_tokens=10, out_tokens=5):
    """Um run cujo turno final é um assistant limpo (sem tool_calls)."""
    return _FakeRunOutput([
        Message(role="user", content="oi"),
        Message(role="assistant", content=text),
    ], in_tokens, out_tokens)


_TOOL_CALL = [{"id": "1", "type": "function",
              "function": {"name": "save_contact_info", "arguments": "{}"}}]


def _dangling_tool_output():
    """Tool rodou mas o turno pós-tool voltou vazio — precisa de forced
    follow-up (ver agno_engine._needs_forced_followup)."""
    return _FakeRunOutput([
        Message(role="assistant", content="vou verificar...", tool_calls=_TOOL_CALL),
        Message(role="tool", content="dados salvos"),
        Message(role="assistant", content="", tool_calls=None),
    ])


class _FakeRunner:
    """Substituto do retorno de build_runner(). Opcionalmente invoca um
    entrypoint de tool REAL primeiro (functions vem do build_functions de
    verdade), populando ``executed`` como em produção."""

    def __init__(self, output, *, functions=None, tool_name=None, tool_args=None):
        self._output = output
        self._functions = functions or {}
        self._tool_name = tool_name
        self._tool_args = tool_args or {}

    async def arun(self, input=None):
        if self._tool_name:
            await self._functions[self._tool_name].entrypoint(**self._tool_args)
        return self._output

    def run(self, input=None):
        if self._tool_name:
            self._functions[self._tool_name].entrypoint(**self._tool_args)
        return self._output


class _FakeFollowupAgent:
    """Substituto do retorno de _build_followup_agent(): uma saída (ou
    exceção) canned, e grava o ``input`` recebido para inspeção."""

    def __init__(self, output=None, exc=None):
        self._output = output
        self._exc = exc
        self.received_input = None

    async def arun(self, input=None):
        self.received_input = input
        if self._exc:
            raise self._exc
        return self._output

    def run(self, input=None):
        self.received_input = input
        if self._exc:
            raise self._exc
        return self._output


class _FollowupFactory:
    """Alvo do monkeypatch de agno_engine._build_followup_agent: devolve os
    agentes canned NA ORDEM (uma chamada por vez — espelha forced-followup
    seguido de script-retry na mesma rodada) e conta as chamadas."""

    def __init__(self, *agents):
        self._queue = list(agents)
        self.calls = 0

    def __call__(self, handler, system_prompt, model_config):
        self.calls += 1
        if not self._queue:
            raise AssertionError("unexpected extra _build_followup_agent call")
        return self._queue.pop(0)


def _call_engine(is_async, *a, **kw):
    fn = agno_engine.run_async if is_async else agno_engine.run_sync
    result = fn(*a, **kw)
    return asyncio.run(result) if asyncio.iscoroutine(result) else result


MESSAGES = [
    {"role": "system", "content": "Você é a BIA, fale só em português."},
    {"role": "user", "content": "Qual seu objetivo com o combo?"},
]

_DIRTY = "Qual seu objetivo com o combo այսօր?"
_CLEAN = "Qual seu objetivo com o combo hoje?"


@pytest.fixture(params=[True, False], ids=["async", "sync"])
def is_async(request):
    return request.param


# ── Caracterização (rodar verde ANTES de editar agent/agno_engine.py) ──────
# As duas seguem valendo DEPOIS: nenhuma aciona a trava nova (a 1ª porque o
# texto é limpo; a 2ª porque o forced-followup por si só não mexe em
# alfabeto — sua reply "Pronto, salvei seus dados!" é latina).

def test_caracterizacao_resposta_limpa_sem_chamada_extra(_engine_ready, monkeypatch, is_async):
    output = _clean_output("Claro, posso ajudar!")
    monkeypatch.setattr(agno_engine, "build_runner", lambda *a, **k: _FakeRunner(output))
    followups = _FollowupFactory()
    monkeypatch.setattr(agno_engine, "_build_followup_agent", followups)

    result = _call_engine(is_async, _FakeHandler(), {"id": 1}, "5511999999999", MESSAGES, [])

    assert result.reply == "Claro, posso ajudar!"
    assert followups.calls == 0


def test_caracterizacao_forced_followup_continua_funcionando(_engine_ready, monkeypatch, is_async):
    schema = {"type": "function", "function": {
        "name": "save_contact_info", "description": "salva",
        "parameters": {"type": "object", "properties": {}}}}
    real_build_functions = agno_engine.build_functions
    captured = {}

    def _spy_build_functions(*a, **k):
        fns = real_build_functions(*a, **k)
        captured["functions"] = fns
        return fns

    monkeypatch.setattr(agno_engine, "build_functions", _spy_build_functions)
    monkeypatch.setattr(
        agno_engine, "build_runner",
        lambda *a, **k: _FakeRunner(_dangling_tool_output(),
                                    functions=captured["functions"],
                                    tool_name="save_contact_info"))
    followups = _FollowupFactory(_FakeFollowupAgent(
        output=_clean_output("Pronto, salvei seus dados!", in_tokens=7, out_tokens=3)))
    monkeypatch.setattr(agno_engine, "_build_followup_agent", followups)

    result = _call_engine(is_async, _FakeHandler(), {"id": 1}, "5511999999999",
                          MESSAGES, [schema])

    assert result.reply == "Pronto, salvei seus dados!"
    assert followups.calls == 1
    assert result.usage == {"prompt_tokens": 17, "completion_tokens": 8, "total_tokens": 25}


# ── Comportamento da trava ───────────────────────────────────────────────

def test_suja_regeneracao_limpa_substitui_e_soma_usage(_engine_ready, monkeypatch, is_async):
    primary = _clean_output(_DIRTY, in_tokens=10, out_tokens=5)
    monkeypatch.setattr(agno_engine, "build_runner", lambda *a, **k: _FakeRunner(primary))
    agent = _FakeFollowupAgent(output=_clean_output(_CLEAN, in_tokens=8, out_tokens=4))
    followups = _FollowupFactory(agent)
    monkeypatch.setattr(agno_engine, "_build_followup_agent", followups)

    result = _call_engine(is_async, _FakeHandler(), {"id": 1}, "5511999999999", MESSAGES, [])

    assert result.reply == _CLEAN
    assert followups.calls == 1
    assert result.usage == {"prompt_tokens": 18, "completion_tokens": 9, "total_tokens": 27}


def test_suja_duas_vezes_mantem_original_e_loga_error(_engine_ready, monkeypatch, is_async):
    primary = _clean_output(_DIRTY, in_tokens=10, out_tokens=5)
    monkeypatch.setattr(agno_engine, "build_runner", lambda *a, **k: _FakeRunner(primary))
    agent = _FakeFollowupAgent(output=_clean_output(_DIRTY, in_tokens=8, out_tokens=4))
    followups = _FollowupFactory(agent)
    monkeypatch.setattr(agno_engine, "_build_followup_agent", followups)
    # caplog não enxerga este logger: a fixture _engine_ready roda Alembic, cujo
    # env.py chama logging.config.fileConfig(...) com disable_existing_loggers
    # default (True) — desliga o logger "agent.agno_engine" (já existente por
    # causa do `from agent import agno_engine` no topo do arquivo) pro resto do
    # processo. Pré-existente ao plano 167; contornado com um spy direto.
    errors = []
    monkeypatch.setattr(agno_engine.logger, "error", lambda *a, **k: errors.append((a, k)))

    result = _call_engine(is_async, _FakeHandler(), {"id": 1}, "5511999999999", MESSAGES, [])

    assert result.reply == _DIRTY
    assert followups.calls == 1
    # soma o usage mesmo quando a 2ª tentativa também reprova (houve resposta)
    assert result.usage == {"prompt_tokens": 18, "completion_tokens": 9, "total_tokens": 27}
    assert len(errors) == 1
    assert "keeping original reply" in errors[0][0][0]


def test_regeneracao_lanca_excecao_mantem_original_e_usage_primario(_engine_ready, monkeypatch, is_async):
    primary = _clean_output(_DIRTY, in_tokens=10, out_tokens=5)
    monkeypatch.setattr(agno_engine, "build_runner", lambda *a, **k: _FakeRunner(primary))
    agent = _FakeFollowupAgent(exc=RuntimeError("proxy fora do ar"))
    followups = _FollowupFactory(agent)
    monkeypatch.setattr(agno_engine, "_build_followup_agent", followups)

    result = _call_engine(is_async, _FakeHandler(), {"id": 1}, "5511999999999", MESSAGES, [])

    assert result.reply == _DIRTY
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_regeneracao_devolve_vazio_mantem_original(_engine_ready, monkeypatch, is_async):
    primary = _clean_output(_DIRTY, in_tokens=10, out_tokens=5)
    monkeypatch.setattr(agno_engine, "build_runner", lambda *a, **k: _FakeRunner(primary))
    agent = _FakeFollowupAgent(output=_clean_output("", in_tokens=3, out_tokens=0))
    followups = _FollowupFactory(agent)
    monkeypatch.setattr(agno_engine, "_build_followup_agent", followups)

    result = _call_engine(is_async, _FakeHandler(), {"id": 1}, "5511999999999", MESSAGES, [])

    assert result.reply == _DIRTY
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


_DIRTY_ARRAY = '["Qual seu objetivo com o combo \\u0561\\u0575\\u057d\\u0585\\u0580?"]'
_CLEAN_ARRAY = '["Qual seu objetivo com o combo hoje?"]'


def test_split_com_escape_e_detectado_e_regeneracao_mantem_formato(_engine_ready, monkeypatch, is_async):
    """Prova do I2: o texto CRU do array é só ASCII (\\uXXXX é uma sequência
    de dígitos/letras latinas) — só quem decodifica o JSON vê o armênio."""
    primary = _clean_output(_DIRTY_ARRAY, in_tokens=10, out_tokens=5)
    monkeypatch.setattr(agno_engine, "build_runner", lambda *a, **k: _FakeRunner(primary))
    agent = _FakeFollowupAgent(output=_clean_output(_CLEAN_ARRAY, in_tokens=8, out_tokens=4))
    followups = _FollowupFactory(agent)
    monkeypatch.setattr(agno_engine, "_build_followup_agent", followups)

    result = _call_engine(is_async, _FakeHandler(), {"id": 1}, "5511999999999", MESSAGES, [])

    assert followups.calls == 1  # só dispara se _guard_text decodificar o array
    assert result.reply == _CLEAN_ARRAY
    assert parse_split_reply(result.reply) == ["Qual seu objetivo com o combo hoje?"]


def test_followup_seguido_de_trava_usa_saida_do_followup(_engine_ready, monkeypatch, is_async):
    """I6: a re-geração parte do ÚLTIMO output aceito (o do forced-followup),
    não do run_output original — prova pela mensagem que chega à retry."""
    schema = {"type": "function", "function": {
        "name": "save_contact_info", "description": "salva",
        "parameters": {"type": "object", "properties": {}}}}
    real_build_functions = agno_engine.build_functions
    captured = {}

    def _spy_build_functions(*a, **k):
        fns = real_build_functions(*a, **k)
        captured["functions"] = fns
        return fns

    monkeypatch.setattr(agno_engine, "build_functions", _spy_build_functions)
    monkeypatch.setattr(
        agno_engine, "build_runner",
        lambda *a, **k: _FakeRunner(_dangling_tool_output(),
                                    functions=captured["functions"],
                                    tool_name="save_contact_info"))

    followup_dirty_output = _FakeRunOutput(
        [Message(role="assistant", content=_DIRTY)], in_tokens=7, out_tokens=3)
    followup_agent = _FakeFollowupAgent(output=followup_dirty_output)
    retry_agent = _FakeFollowupAgent(output=_clean_output(_CLEAN, in_tokens=6, out_tokens=2))
    followups = _FollowupFactory(followup_agent, retry_agent)
    monkeypatch.setattr(agno_engine, "_build_followup_agent", followups)

    result = _call_engine(is_async, _FakeHandler(), {"id": 1}, "5511999999999",
                          MESSAGES, [schema])

    assert followups.calls == 2
    assert result.reply == _CLEAN
    retry_input = retry_agent.received_input
    assert retry_input[-2].content == _DIRTY  # veio do fu_output, não do run_output
    assert retry_input[-1].role == "user"


def test_entrada_corretiva_tem_user_final_com_amostra_e_sem_system(_engine_ready, monkeypatch, is_async):
    primary = _clean_output(_DIRTY)
    monkeypatch.setattr(agno_engine, "build_runner", lambda *a, **k: _FakeRunner(primary))
    clean_agent = _FakeFollowupAgent(output=_clean_output(_CLEAN))
    followups = _FollowupFactory(clean_agent)
    monkeypatch.setattr(agno_engine, "_build_followup_agent", followups)

    _call_engine(is_async, _FakeHandler(), {"id": 1}, "5511999999999", MESSAGES, [])

    received = clean_agent.received_input
    assert received[-1].role == "user"
    # cita a amostra reprovada (foreign_sample ORDENA as letras únicas, então
    # não é necessariamente a substring na ordem original — daí checar por
    # caractere, não pela palavra inteira).
    assert all(ch in received[-1].content for ch in "այսօր")
    assert all(m.role != "system" for m in received)


def test_nome_estilizado_sujo_2x_envia_original_sem_excecao(_engine_ready, monkeypatch, is_async):
    """Não-regressão do risco 3 do plano: um nome de contato estilizado
    ecoado na reply se comporta como qualquer outra "sujeira" — retry, e se
    continuar sujo, envia a original sem quebrar o turno."""
    dirty = "Prazer, Júηiør! Como posso ajudar?"
    primary = _clean_output(dirty)
    monkeypatch.setattr(agno_engine, "build_runner", lambda *a, **k: _FakeRunner(primary))
    still_dirty_agent = _FakeFollowupAgent(output=_clean_output(dirty))
    followups = _FollowupFactory(still_dirty_agent)
    monkeypatch.setattr(agno_engine, "_build_followup_agent", followups)

    result = _call_engine(is_async, _FakeHandler(), {"id": 1}, "5511999999999", MESSAGES, [])

    assert result.reply == dirty
    assert followups.calls == 1


def test_cancelled_error_na_regeneracao_propaga(_engine_ready, monkeypatch):
    """CancelledError é BaseException — o except Exception do bloco não pode
    engolir um cancelamento do turno (plano 96 depende disso)."""
    primary = _clean_output(_DIRTY)
    monkeypatch.setattr(agno_engine, "build_runner", lambda *a, **k: _FakeRunner(primary))
    boom_agent = _FakeFollowupAgent(exc=asyncio.CancelledError())
    followups = _FollowupFactory(boom_agent)
    monkeypatch.setattr(agno_engine, "_build_followup_agent", followups)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agno_engine.run_async(
            _FakeHandler(), {"id": 1}, "5511999999999", MESSAGES, []))
