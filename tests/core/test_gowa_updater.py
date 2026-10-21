"""Atualizador do binário do GOWA (plugin ``gowa``, ``updater.py``) e o link do alerta.

Sem rede, sem banco, sem subprocesso real: o "GitHub" é uma pasta local servida por
``file://`` (``urllib`` aceita o esquema) e o ``runtime`` é um registrador. O zip é
montado na hora, com um binário de mentira que sai com 0 — o ``_smoke_test`` do
atualizador executa o arquivo de verdade, então ele precisa ser executável.

Rodar::

    venv/bin/python -m pytest -q tests/core/test_gowa_updater.py
"""

from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from tests.plugin_test_utils import load_plugin_module

PKG = "gowa_updater_under_test"


@pytest.fixture(scope="module")
def upd():
    return load_plugin_module("gowa", "updater", package_name=PKG)


@pytest.fixture(scope="module")
def alerts():
    return load_plugin_module("gowa", "alerts", package_name=PKG)


# ── bancada ──────────────────────────────────────────────────────────────────

class FakeRuntime:
    """Registra stop/start e devolve o que o teste mandar."""

    def __init__(self, root: Path, *, healthy=True, running="8.11.0"):
        self.root, self.healthy, self.running = root, healthy, running
        self.calls: list[str] = []

    def stop(self):
        self.calls.append("stop")

    def start(self):
        self.calls.append("start")

    def wait_healthy(self, timeout):
        self.calls.append("health")
        # Só a 1ª verificação (a da versão NOVA) falha; a do rollback tem de passar
        # para o teste poder provar que o anterior voltou.
        if self.healthy is False and self.calls.count("health") == 1:
            return False
        return True

    def running_version(self):
        return self.running

    def session_root(self):
        return self.root


@pytest.fixture
def bench(tmp_path, monkeypatch, upd):
    """Raiz isolada + 'GitHub' local. Devolve um objeto com helpers."""
    root = tmp_path / "app"
    (root / "bin").mkdir(parents=True)
    (root / "storages").mkdir()
    # Binário bundled (8.11.0), como a imagem Docker / o launcher deixariam.
    (root / "bin" / upd.binary_name()).write_bytes(b"#!/bin/sh\nexit 0\n")
    (root / "bin" / upd.binary_name()).chmod(0o755)
    (root / "bin" / ".gowa_stamp").write_text("8.11.0\n")

    monkeypatch.setattr(upd, "repo_root", lambda: root)
    monkeypatch.setattr(upd, "_muted_until", 0.0)
    monkeypatch.setattr(upd, "_MUTE_AFTER_SEC", 0)
    upd._state.update({"status": "idle", "phase": "", "progress": 0,
                       "version": "", "message": "", "last_result": None})

    gh = tmp_path / "github"
    gh.mkdir()
    monkeypatch.setattr(upd, "GOWA_RELEASE_DOWNLOAD", f"file://{gh}/{{asset}}")

    class B:
        pass

    b = B()
    b.root, b.gh, b.upd = root, gh, upd

    def publish(version="9.6.0", *, body=b"#!/bin/sh\nexit 0\n", tamper=False, checksums=True):
        os_, arch = upd.detect_platform()
        asset = upd.asset_name(version, os_, arch)
        zpath = gh / asset
        with zipfile.ZipFile(zpath, "w") as zf:
            info = zipfile.ZipInfo(upd.inner_binary_name(os_, arch))
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            zf.writestr(info, body)
            zf.writestr("readme.md", "x")
        digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
        if tamper:
            digest = "0" * 64
        if checksums:
            (gh / upd.checksums_asset(os_)).write_text(f"{digest}  {asset}\n")
        return asset

    b.publish = publish
    return b


# ── versões e faixa ──────────────────────────────────────────────────────────

def test_parse_e_comparacao_de_versao(upd):
    assert upd.parse_version("v9.6.0") == (9, 6, 0)
    assert upd.parse_version("9.1.0-rc1") == (9, 1, 0)
    assert upd.parse_version("lixo") == (0, 0, 0)
    assert upd.version_cmp("9.6.0", "8.11.0") == 1
    assert upd.version_cmp("8.11.0", "8.11.0") == 0
    assert upd.version_cmp("8.9.9", "8.11.0") == -1  # numérico, não lexicográfico


def test_faixa_homologada(upd):
    assert upd.is_supported("8.11.0") and upd.is_supported("9.6.0")
    assert not upd.is_supported("8.10.9")     # abaixo do piso
    assert not upd.is_supported("10.0.0")     # próximo major: ainda não avaliado
    assert not upd.is_supported("")


def test_preflight_recusa_formato_invalido_e_pede_confirmacao_fora_da_faixa(upd):
    assert upd.preflight("9.6")["ok"] is False                        # não é x.y.z
    assert upd.preflight("9.6.0; rm -rf /")["ok"] is False            # nunca vira URL
    pedido = upd.preflight("10.0.0")
    assert pedido["requires_confirmation"] is True
    assert upd.preflight("10.0.0", force_unsupported=True) is None    # confirmado
    assert upd.preflight("9.6.0") is None


# ── resolução do binário ─────────────────────────────────────────────────────

def test_resolve_prefere_o_bundled_sem_gerenciado(bench):
    path, source, version = bench.upd.resolve_binary()
    assert source == "bundled" and version == "8.11.0"
    assert path == bench.root / "bin" / bench.upd.binary_name()


def test_resolve_prefere_o_gerenciado_mais_novo(bench):
    u = bench.upd
    u.managed_dir(create=True)
    u.managed_binary_path().write_bytes(b"x")
    u.write_meta({"version": "9.6.0"})
    _, source, version = u.resolve_binary()
    assert (source, version) == ("managed", "9.6.0")


def test_resolve_ignora_gerenciado_velho_esquecido(bench):
    """Imagem reconstruída com GOWA mais novo vence um override antigo."""
    u = bench.upd
    (bench.root / "bin" / ".gowa_stamp").write_text("9.6.0\n")
    u.managed_dir(create=True)
    u.managed_binary_path().write_bytes(b"x")
    u.write_meta({"version": "8.11.0"})
    assert u.resolve_binary()[1] == "bundled"


def test_apply_binary_troca_so_o_executavel(bench):
    u = bench.upd
    cmd = ["/x/bin/gowa", "rest", "--port", "1"]
    assert u.apply_binary(cmd) == cmd                      # sem gerenciado: intacto
    u.managed_dir(create=True)
    u.managed_binary_path().write_bytes(b"x")
    u.write_meta({"version": "9.6.0"})
    out = u.apply_binary(cmd)
    assert out[0] == str(u.managed_binary_path()) and out[1:] == cmd[1:]


# ── instalação ───────────────────────────────────────────────────────────────

def test_instala_atualiza_e_registra(bench):
    bench.publish("9.6.0")
    rt = FakeRuntime(bench.root, running="8.11.0")
    # depois de subir, o processo "diz" a versão nova
    orig_start = rt.start
    rt.start = lambda: (orig_start(), setattr(rt, "running", "9.6.0"))

    res = bench.upd.install("9.6.0", runtime=rt)

    assert res["ok"] is True and res["version"] == "9.6.0"
    assert res["previous_version"] == "8.11.0" and res["rolled_back"] is False
    assert rt.calls[:2] == ["stop", "start"]
    path, source, version = bench.upd.resolve_binary()
    assert (source, version) == ("managed", "9.6.0")
    meta = bench.upd.read_meta()
    assert meta["checksum"] == "verified" and meta["previous_version"] == "8.11.0"
    assert bench.upd.is_busy() is False
    assert bench.upd.get_state()["last_result"]["ok"] is True
    # o staging não sobra
    assert not any(bench.upd.tmp_dir().glob("*"))


def test_checksum_divergente_cancela_sem_tocar_em_nada(bench):
    bench.publish("9.6.0", tamper=True)
    rt = FakeRuntime(bench.root)
    res = bench.upd.install("9.6.0", runtime=rt)
    assert res["ok"] is False and "checksum" in res["message"]
    assert rt.calls == []                                   # nem parou o GOWA
    assert bench.upd.resolve_binary()[1] == "bundled"


def test_sem_checksum_publicado_nao_instala(bench):
    """Binário executável vindo da rede sem verificação: recusa."""
    bench.publish("9.6.0", checksums=False)
    rt = FakeRuntime(bench.root)
    res = bench.upd.install("9.6.0", runtime=rt)
    assert res["ok"] is False and rt.calls == []
    assert not bench.upd.managed_binary_path().exists()


def test_health_check_falho_volta_ao_binario_anterior(bench):
    bench.publish("9.6.0")
    rt = FakeRuntime(bench.root, healthy=False)
    res = bench.upd.install("9.6.0", runtime=rt)
    assert res["ok"] is False and res["rolled_back"] is True
    # parou, subiu o novo (falhou o health), parou, subiu o anterior
    assert rt.calls.count("stop") == 2 and rt.calls.count("start") == 2
    path, source, version = bench.upd.resolve_binary()
    assert (source, version) == ("bundled", "8.11.0")
    assert not bench.upd.managed_binary_path().exists()


def test_rollback_restaura_o_gerenciado_anterior(bench):
    """9.5.0 gerenciado → atualiza p/ 9.6.0 → reverte: volta ao 9.5.0, não ao bundled."""
    u = bench.upd
    u.managed_dir(create=True)
    u.managed_binary_path().write_bytes(b"#!/bin/sh\nexit 0\n")
    u.write_meta({"version": "9.5.0"})
    bench.publish("9.6.0")
    rt = FakeRuntime(bench.root, running="9.5.0")

    assert u.install("9.6.0", runtime=rt)["ok"] is True
    assert u.backup_version() == "9.5.0"

    res = u.rollback(runtime=rt)
    assert res["ok"] is True
    assert u.resolve_binary()[2] == "9.5.0"


def test_major_faz_snapshot_da_sessao_inclusive_dos_dedicados_e_wal(bench):
    root = bench.root
    (root / "storages" / "whatsapp.db").write_bytes(b"compartilhado")
    (root / "storages" / "whatsapp.db-wal").write_bytes(b"wal")
    ded = root / "storages" / "gowa_ch_abc" / "storages"
    ded.mkdir(parents=True)
    (ded / "whatsapp.db").write_bytes(b"dedicado")
    (root / "storages" / "whatsbot.db").write_bytes(b"NAO-E-DO-GOWA")

    files = {p.relative_to(root).as_posix() for p in bench.upd.session_files(root)}
    assert files == {"storages/whatsapp.db", "storages/whatsapp.db-wal",
                     "storages/gowa_ch_abc/storages/whatsapp.db"}

    bench.publish("9.6.0")
    rt = FakeRuntime(bench.root, running="8.11.0")
    assert bench.upd.install("9.6.0", runtime=rt)["ok"] is True
    snap = Path(bench.upd.read_meta()["session_backup"])
    assert (snap / "storages" / "whatsapp.db").read_bytes() == b"compartilhado"
    assert (snap / "storages" / "gowa_ch_abc" / "storages" / "whatsapp.db").exists()

    # O GOWA novo "migrou" o banco; o rollback tem de devolver o do snapshot.
    (root / "storages" / "whatsapp.db").write_bytes(b"MIGRADO")
    (root / "storages" / "whatsapp.db-wal").write_bytes(b"wal-do-schema-novo")
    assert bench.upd.rollback(runtime=rt)["ok"] is True
    assert (root / "storages" / "whatsapp.db").read_bytes() == b"compartilhado"
    assert (root / "storages" / "whatsapp.db-wal").read_bytes() == b"wal"


def test_minor_nao_faz_snapshot(bench):
    (bench.root / "storages" / "whatsapp.db").write_bytes(b"x")
    (bench.root / "bin" / ".gowa_stamp").write_text("9.5.0\n")
    bench.publish("9.6.0")
    rt = FakeRuntime(bench.root, running="9.5.0")
    assert bench.upd.install("9.6.0", runtime=rt)["ok"] is True
    assert bench.upd.read_meta()["session_backup"] == ""


def test_falha_no_backup_religa_o_gowa_e_nao_apaga_o_gerenciado(bench, monkeypatch):
    """Falha ENTRE parar e trocar: nada a desfazer, só religar — e o gerenciado bom
    que já existia não pode ser tocado."""
    u = bench.upd
    u.managed_dir(create=True)
    u.managed_binary_path().write_bytes(b"#!/bin/sh\nexit 0\n")
    u.write_meta({"version": "8.12.0"})
    (bench.root / "storages" / "whatsapp.db").write_bytes(b"x")
    bench.publish("9.6.0")

    def boom(*_a, **_k):
        raise u.GowaUpdateError("disco cheio")
    monkeypatch.setattr(u, "_backup_session", boom)
    rt = FakeRuntime(bench.root, running="8.12.0")

    res = u.install("9.6.0", runtime=rt)
    assert res["ok"] is False and "disco cheio" in res["message"]
    assert rt.calls[0] == "stop" and "start" in rt.calls      # religou
    assert u.managed_binary_path().exists()                   # gerenciado bom intacto
    assert u.read_meta()["version"] == "8.12.0"


def test_nao_roda_duas_operacoes_ao_mesmo_tempo(bench):
    assert bench.upd._LOCK.acquire(blocking=False)
    try:
        with pytest.raises(bench.upd.GowaUpdateBusy):
            bench.upd.install("9.6.0", runtime=FakeRuntime(bench.root))
        with pytest.raises(bench.upd.GowaUpdateBusy):
            bench.upd.rollback(runtime=FakeRuntime(bench.root))
    finally:
        bench.upd._LOCK.release()


def test_alerta_fica_mudo_durante_e_logo_apos_a_troca(bench, monkeypatch):
    u = bench.upd
    assert u.alerts_muted() is False
    monkeypatch.setattr(u, "_MUTE_AFTER_SEC", 90)
    bench.publish("9.6.0")
    assert u.install("9.6.0", runtime=FakeRuntime(bench.root))["ok"] is True
    assert u.alerts_muted() is True       # os números ainda estão reconectando


# ── link do alerta de desconexão ─────────────────────────────────────────────

def test_link_do_alerta_abre_canais_no_qr_do_canal_caido(alerts):
    msg = alerts._msg_disconnected(
        [{"id": "gowa_ab12", "name": "Equipe_01", "phone": "556493359874"},
         {"id": "gowa_cd34", "name": "Equipe_02", "phone": ""}],
        "https://atendimento.exemplo.com.br/")
    assert "/gowa/config" not in msg                        # o link morto de antes
    assert 'href="https://atendimento.exemplo.com.br/channels/gowa_ab12?connect=1"' in msg
    assert 'href="https://atendimento.exemplo.com.br/channels/gowa_cd34?connect=1"' in msg
    assert msg.count("Reconectar agora") == 2               # um por caixa caída


def test_link_sem_id_cai_na_lista_de_canais(alerts):
    assert alerts._reconnect_url("https://x.com") == "https://x.com/channels"
    assert alerts._reconnect_url("https://x.com/", "a b") == "https://x.com/channels/a%20b?connect=1"


# ── ponte real: GowaRuntime sobre um ManagedProcess de verdade ───────────────

@pytest.fixture(scope="module")
def life():
    return load_plugin_module("gowa", "lifecycle", package_name=PKG)


def test_runtime_reinicia_o_processo_real_sem_duplicar_o_watchdog(life, upd, tmp_path, monkeypatch):
    """stop() → start() no MESMO ManagedProcess: pid novo, executável reapontado e
    UM watchdog só. Dois watchdogs no mesmo processo religariam em dobro ao cair."""
    import importlib
    import sys
    import threading
    from types import SimpleNamespace

    from runtime.subprocess_service import ManagedProcess, SubprocessSpec

    processes = importlib.import_module(f"{PKG}.processes")
    monkeypatch.setattr(processes, "_MANAGED", {})
    monkeypatch.setattr(upd, "repo_root", lambda: tmp_path)   # sem gerenciado: argv intacto

    nome = "gowa_rt_test"
    spec = SubprocessSpec(
        name=nome, cmd=[sys.executable, "-c", "import time; time.sleep(120)"],
        pid_file=tmp_path / "rt.pid", restart_delay=0.1)
    mp = ManagedProcess(spec)

    def watchdogs():
        return [t for t in threading.enumerate()
                if t.name == f"{nome}-watchdog" and t.is_alive()]

    religou = []
    deps = SimpleNamespace(
        gowa_manager=SimpleNamespace(port=1, _on_restart=lambda: religou.append(1)),
        gowa_client=SimpleNamespace(health_check=lambda: True))
    rt = life.GowaRuntime(deps, mp)
    try:
        mp.start()
        pid1 = mp.status()["pid"]
        assert len(watchdogs()) == 1

        rt.stop()
        assert not mp.is_running()
        assert watchdogs() == []                 # o watchdog velho morreu ANTES do start

        rt.start()
        assert mp.is_running() and mp.status()["pid"] != pid1
        assert len(watchdogs()) == 1             # um só
        assert religou == [1]                    # zerou cliente/QR como no restart normal
        assert spec.cmd[0] == sys.executable     # sem gerenciado, o argv não muda
        assert rt.wait_healthy(2) is True
    finally:
        mp.stop()
