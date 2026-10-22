"""Offline tests for server/file_manager.py (plano 179).

No server, no database: only the filesystem logic that decides what the user can see
and delete. Port of the WhatsBot-Lite suite, adapted to the Pro layout (flat roots,
``statics/outbox``), plus ``.f4v`` and the real-disk numbers.

    venv/bin/python -m pytest tests/core/test_file_manager.py -q
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import pytest

from server import file_manager as fm


def forge(root: str, rel: str) -> str:
    return base64.urlsafe_b64encode(f"{root}:{rel}".encode()).decode().rstrip("=")


def write(path: Path, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def refused(manager, file_id) -> bool:
    try:
        manager.resolve(file_id)
    except fm.FileAccessError:
        return True
    return False


@pytest.fixture
def tree(tmp_path):
    data = tmp_path / "app"
    media = data / "statics" / "media"
    outbox = data / "statics" / "outbox"
    storages = data / "storages"
    write(media / "a.jpg", 1000)
    write(media / "b.ogg", 300)
    write(media / "c.mp4", 5000)
    write(media / "d.pdf", 200)
    write(outbox / "1.png", 700)
    # things that must NEVER be reachable
    write(storages / "whatsbot.db", 9999)
    write(storages / "plugins" / "x" / "tools.py", 77)
    write(data / ".env", 50)
    secret = tmp_path / "outside.txt"
    secret.write_text("secret")
    return {
        "data": data, "media": media, "outbox": outbox, "storages": storages,
        "secret": secret, "manager": fm.FileManager(data, cache_ttl=0),
    }


# ── kind / usage ─────────────────────────────────────────────────────────

def test_kind_detection():
    assert fm.kind_of("A.JPG") == "image"
    assert fm.kind_of("x.ogg") == "audio"
    assert fm.kind_of("x.mp4") == "video"
    assert fm.kind_of("x.pdf") == "document"
    assert fm.kind_of("README") == "document"
    for name in ("a.jfif", "a.JFIF", "a.jpe", "a.pjpeg", "a.avif", "a.tif", "a.tiff"):
        assert fm.kind_of(name) == "image", name


def test_f4v_is_video_and_previews_as_mp4(tmp_path):
    """O GOWA do Pro grava vídeo como ``.f4v``."""
    assert fm.kind_of("1792-uuid.f4v") == "video"
    p = tmp_path / "x.f4v"
    p.write_bytes(b"\x00\x00\x00\x18ftypf4v ")
    assert fm.preview_mime(p) == "video/mp4"


def test_roots_are_media_and_outbox(tree):
    assert fm.ROOTS == {"received": "media", "sent": "outbox"}
    assert tree["manager"].roots["sent"] == (tree["data"] / "statics" / "outbox").resolve()


def test_usage_and_scope(tree):
    u = tree["manager"].usage()
    assert u["roots"]["received"] == {"files": 4, "bytes": 6500}
    assert u["roots"]["sent"] == {"files": 1, "bytes": 700}
    assert u["deletable_bytes"] == 7200
    assert u["system_bytes"] >= 9999 + 77
    assert u["system_bytes"] < 9999 + 77 + 7200  # the media roots are not "system"


def test_usage_without_system_skips_the_walk(tree):
    u = tree["manager"].usage(include_system=False)
    assert "system_bytes" not in u and "total_bytes" not in u
    assert u["roots"]["received"]["files"] == 4


def test_disk_reports_the_real_volume(tree):
    d = tree["manager"].disk()
    assert d is not None
    assert d["total"] > 0 and d["used"] >= 0 and d["free"] >= 0
    assert d["used"] + d["free"] <= d["total"] * 1.01
    assert tree["manager"].usage()["disk"] == d or tree["manager"].usage()["disk"]["total"] == d["total"]


# ── listing ──────────────────────────────────────────────────────────────

def test_listing_is_flat_with_ids_and_no_absolute_path(tree):
    m = tree["manager"]
    page = m.list_entries("received")
    assert {r["name"] for r in page["items"]} == {"a.jpg", "b.ogg", "c.mp4", "d.pdf"}
    assert all(r["type"] == "file" for r in page["items"])
    assert all(str(tree["data"]) not in str(r) for r in page["items"])
    assert all("url" not in r for r in page["items"])  # Pro serves media by route only


def test_sorting_filtering_and_paging(tree):
    m = tree["manager"]
    heavy = m.list_entries("received", sort="size", order="desc")
    assert [r["name"] for r in heavy["items"]][:2] == ["c.mp4", "a.jpg"]
    light = m.list_entries("received", sort="size", order="asc")
    assert light["items"][0]["name"] == "d.pdf"
    only_video = m.list_entries("received", kind="video")
    assert [r["name"] for r in only_video["items"]] == ["c.mp4"]
    paged = m.list_entries("received", limit=3)
    assert paged["next_offset"] == 3 and len(paged["items"]) == 3
    last = m.list_entries("received", limit=3, offset=3)
    assert last["next_offset"] is None and len(last["items"]) == 1
    assert paged["total"] == 4 and paged["total_bytes"] == 6500


def test_sent_root_lists_the_outbox(tree):
    page = tree["manager"].list_entries("sent")
    assert [r["name"] for r in page["items"]] == ["1.png"]


def test_db_path_round_trip(tree):
    m = tree["manager"]
    assert m.db_path("sent", "1.png") == "statics/outbox/1.png"
    assert m.rel_from_db_path("sent", "statics/outbox/1.png") == "1.png"
    assert m.rel_from_db_path("sent", "/statics/outbox/1.png") == "1.png"  # legacy slash
    assert m.rel_from_db_path("sent", "statics/media/1.png") is None
    assert m.rel_from_db_path("sent", "statics/outbox/") is None
    assert m.id_for_db_path("statics/media/a.jpg") == forge("received", "a.jpg")
    assert m.id_for_db_path("statics/avatars/a.jpg") is None
    assert m.id_for_db_path("geo:1,2") is None


# ── security ─────────────────────────────────────────────────────────────

def test_forged_and_hostile_ids_are_refused(tree):
    m, secret = tree["manager"], tree["secret"]
    assert refused(m, "!!!not base64!!!")
    assert refused(m, "")
    assert refused(m, forge("storages", "whatsbot.db"))
    assert refused(m, forge("received", ""))
    assert refused(m, forge("received", "../../storages/whatsbot.db"))
    assert refused(m, forge("received", "x/../../../.env"))
    assert refused(m, forge("received", "/etc/passwd"))
    assert refused(m, forge("received", str(secret)))
    assert refused(m, forge("received", "..\\..\\storages\\whatsbot.db"))
    assert refused(m, forge("received", "a\x00.jpg"))
    assert refused(m, forge("received", "nope.jpg"))
    assert refused(m, forge("received", "../media_evil/x"))
    assert not refused(m, forge("received", "a.jpg"))


def test_symlinks_are_never_followed_listed_or_deleted(tree):
    m, media, secret, storages = tree["manager"], tree["media"], tree["secret"], tree["storages"]
    link_file, link_dir = media / "link.jpg", media / "linkdir"
    try:
        os.symlink(secret, link_file)
        os.symlink(storages, link_dir)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    assert refused(m, forge("received", "link.jpg"))
    assert refused(m, forge("received", "linkdir"))
    assert refused(m, forge("received", "linkdir/whatsbot.db"))
    names = {r["name"] for r in m.list_entries("received")["items"]}
    assert "link.jpg" not in names and "whatsbot.db" not in names
    res = m.delete([forge("received", "link.jpg"), forge("received", "linkdir")])
    assert res.files == 0 and res.failed == 2
    assert secret.exists() and (storages / "whatsbot.db").exists()


def test_symlinked_statics_of_a_dedicated_gowa_is_not_counted_twice(tree):
    """GOWA dedicado por proxy tem um symlink ``statics`` de volta à pasta principal."""
    media = tree["media"]
    try:
        os.symlink(tree["data"] / "statics", media / "statics")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    assert tree["manager"].usage()["roots"]["received"]["files"] == 4


# ── delete ───────────────────────────────────────────────────────────────

def test_delete_one_file_reports_the_db_path(tree):
    m = tree["manager"]
    ok = forge("received", "a.jpg")
    res = m.delete([ok])
    assert (res.files, res.bytes, res.failed) == (1, 1000, 0)
    assert not (tree["media"] / "a.jpg").exists()
    assert res.removed_paths == ["statics/media/a.jpg"]
    again = m.delete([ok])
    assert again.files == 0  # already gone: counts as a refused id, never raises


def test_delete_from_the_sent_root(tree):
    res = tree["manager"].delete([forge("sent", "1.png")])
    assert res.files == 1 and not (tree["outbox"] / "1.png").exists()
    assert res.removed_paths == ["statics/outbox/1.png"]


def test_delete_a_folder_removes_its_files_and_keeps_the_root(tree):
    sub = tree["media"] / "5511" / "2026-10-02"
    write(sub / "x.jpg", 100)
    write(sub / "y.jpg", 50)
    res = tree["manager"].delete([forge("received", "5511")])
    assert res.files == 2 and res.bytes == 150
    assert not (tree["media"] / "5511").exists()
    assert tree["media"].is_dir() and (tree["media"] / "a.jpg").exists()


def test_hostile_ids_delete_nothing(tree):
    before = {p for p in tree["storages"].rglob("*")}
    res = tree["manager"].delete([forge("received", "../../storages"), forge("received", "..")])
    assert res.files == 0 and res.failed == 2
    assert {p for p in tree["storages"].rglob("*")} == before
    assert (tree["data"] / ".env").exists()


def test_too_many_ids_is_refused(tree):
    with pytest.raises(fm.FileAccessError):
        tree["manager"].delete([forge("received", "a.jpg")] * (fm.MAX_DELETE_IDS + 1))


def test_delete_files_revalidates_right_before_unlinking(tree):
    """Um arquivo trocado por symlink depois da listagem não pode ser seguido."""
    m, media, secret = tree["manager"], tree["media"], tree["secret"]
    victim = media / "d.pdf"
    victim.unlink()
    try:
        os.symlink(secret, victim)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    res = m.delete_files([("received", "d.pdf")])
    assert res.files == 0 and res.failed == 1
    assert secret.exists()


def test_delete_invalidates_the_scan_cache(tmp_path):
    data = tmp_path / "app"
    write(data / "statics" / "media" / "a.jpg", 10)
    write(data / "statics" / "media" / "b.jpg", 10)
    m = fm.FileManager(data, cache_ttl=3600)
    assert m.usage()["roots"]["received"]["files"] == 2
    m.delete([forge("received", "a.jpg")])
    assert m.usage()["roots"]["received"]["files"] == 1


# ── preview mime ─────────────────────────────────────────────────────────

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
PDF = b"%PDF-1.4\n1 0 obj"


def _blob(root: Path, name: str, data: bytes) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_preview_mime_by_extension_and_content(tmp_path):
    pv = tmp_path / "pv"
    assert fm.preview_mime(_blob(pv, "a.jfif", JPEG)) == "image/jpeg"
    assert fm.preview_mime(_blob(pv, "a.pjpeg", JPEG)) == "image/jpeg"
    assert fm.preview_mime(_blob(pv, "A.JFIF", JPEG)) == "image/jpeg"
    assert fm.preview_mime(_blob(pv, "a.pdf", PDF)) == "application/pdf"
    assert fm.preview_mime(_blob(pv, "a.opus", b"OggS")) == "audio/ogg"
    assert fm.preview_mime(_blob(pv, "a.oga", b"OggS")) == "audio/ogg"
    assert fm.preview_mime(_blob(pv, "a.mp4", b"\x00\x00\x00\x18ftypmp42")) == "video/mp4"
    for ext in ("txt", "csv", "json", "log", "md"):
        assert fm.preview_mime(_blob(pv, f"a.{ext}", b"hi")) == "text/plain", ext
    assert fm.preview_mime(_blob(pv, "semext1", JPEG)) == "image/jpeg"
    assert fm.preview_mime(_blob(pv, "semext2", PDF)) == "application/pdf"
    assert fm.preview_mime(_blob(pv, "a.bin", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)) == "image/png"
    assert fm.preview_mime(_blob(pv, "semext3", b"\x00\x00\x00\x18ftypheic")) == "image/heic"
    assert fm.preview_mime(_blob(pv, "semext4", "olá, mundo\n".encode())) == "text/plain"
    assert fm.preview_mime(_blob(pv, "semext5", bytes(range(256)) * 4)) is None
    for ext in ("docx", "xlsx", "zip"):
        assert fm.preview_mime(_blob(pv, f"a.{ext}", b"PK\x03\x04")) is None, ext
    assert fm.preview_mime(_blob(pv, "semext6", b"")) is None
    assert fm.preview_mime(pv / "does-not-exist") is None


def test_active_content_is_never_inline(tmp_path):
    pv = tmp_path / "pv"
    assert fm.preview_mime(_blob(pv, "a.html", b"<script>alert(1)</script>")) is None
    for ext in ("htm", "xhtml"):
        assert fm.preview_mime(_blob(pv, f"a.{ext}", b"<b>x</b>")) is None
    assert fm.preview_mime(_blob(pv, "a.svg", b"<svg><script>1</script></svg>")) is None
    assert fm.preview_mime(_blob(pv, "A.SVG", b"<svg/>")) is None


# ── sender names ─────────────────────────────────────────────────────────

def test_clean_name():
    assert fm.clean_name("Teste.mp4") == "Teste.mp4"
    assert fm.clean_name("Relatório final (v2).pdf") == "Relatório final (v2).pdf"
    assert fm.clean_name("../../etc/passwd") == "passwd"
    assert fm.clean_name("..\\..\\evil.exe") == "evil.exe"
    assert fm.clean_name('a<b>c:d"e|f?g*h.txt') == "abcdefgh.txt"
    assert fm.clean_name("a\x00b\nc\x1f.txt") == "abc.txt"
    assert fm.clean_name("  nome.  ") == "nome"
    assert fm.clean_name(".gitignore") == ".gitignore"
    assert fm.clean_name("..") == "" and fm.clean_name("/") == "" and fm.clean_name("   ") == ""
    assert fm.clean_name(None) == "" and fm.clean_name(5) == ""
    long = fm.clean_name("x" * 500 + ".mp4")
    assert len(long) <= fm.MAX_NAME_LEN and long.endswith(".mp4")
    for n in ("a<b>.txt", "../x.y", "  z. ", "Relatório.pdf", "x" * 500 + ".mp4"):
        assert fm.clean_name(fm.clean_name(n)) == fm.clean_name(n)


def test_upload_name_drops_app_made_defaults():
    assert fm.upload_name("voice.ogg") == "" and fm.upload_name("image.png") == ""
    assert fm.upload_name("blob") == "" and fm.upload_name("Image.PNG") == ""
    assert fm.upload_name("foto da obra.jpg") == "foto da obra.jpg"


def test_final_name():
    assert fm.final_name("1792-uuid.mp4", "Teste.mp4") == "Teste.mp4"
    assert fm.final_name("1792-uuid.jpg", None) == "1792-uuid.jpg"
    assert fm.final_name("1792-uuid.jpg", "  ") == "1792-uuid.jpg"
    assert fm.final_name("1792-uuid.pdf", "Relatorio") == "Relatorio.pdf"
    assert fm.final_name("x.pdf", "../../a.pdf") == "a.pdf"


def test_name_from_content():
    assert fm.name_from_content("[Documento recebido: Teste.pdf]") == "Teste.pdf"
    assert fm.name_from_content("[Documento enviado: Orçamento 2026.xlsx]\nsegue") == "Orçamento 2026.xlsx"
    assert fm.name_from_content("[Documento recebido: a [1].pdf]\nlegenda") == "a [1].pdf"
    assert fm.name_from_content("[Documento recebido: documento]") == ""
    assert fm.name_from_content("[Áudio recebido]") == "" and fm.name_from_content("") == ""
    assert fm.name_from_content("oi\n[Documento recebido: x.pdf]") == ""


def test_name_from_content_skips_the_group_author_prefix():
    """Em grupo o autor é o PRIMEIRO elemento do ``content`` (``[Fulano]: ``)."""
    assert fm.name_from_content("[Fulano]: [Documento recebido: Contrato.pdf]") == "Contrato.pdf"


# ── listing with sender names ────────────────────────────────────────────

def test_listing_applies_sender_names(tmp_path):
    data = tmp_path / "app"
    media = data / "statics" / "media"
    for name, size in (("u1.jpg", 40), ("u2.mp4", 50), ("u3.pdf", 60), ("u4.ogg", 70)):
        write(media / name, size)
    m = fm.FileManager(data, cache_ttl=0)
    names = {"statics/media/u1.jpg": "Foto da obra.jpg", "statics/media/u3.pdf": "Contrato.pdf"}

    def resolver(paths):
        return {p: names[p] for p in paths if p in names}

    by_rel = {r["rel"]: r for r in m.list_entries("received", name_resolver=resolver)["items"]}
    assert by_rel["u1.jpg"]["name"] == "Foto da obra.jpg"
    assert by_rel["u2.mp4"]["name"] == "u2.mp4"
    assert base64.urlsafe_b64decode(by_rel["u1.jpg"]["id"] + "==").decode().endswith("u1.jpg")
    by_name = m.list_entries("received", sort="name", order="asc", name_resolver=resolver)
    assert [r["name"] for r in by_name["items"]] == ["Contrato.pdf", "Foto da obra.jpg", "u2.mp4", "u4.ogg"]
    paged = m.list_entries("received", sort="name", order="asc", limit=2, offset=1, name_resolver=resolver)
    assert [r["name"] for r in paged["items"]] == ["Foto da obra.jpg", "u2.mp4"]
    calls: list[int] = []

    def counting(paths):
        calls.append(len(paths))
        return {}

    m.list_entries("received", sort="date", limit=2, name_resolver=counting)
    assert calls == [2]  # another sort resolves only the returned page


# ── period, selector, searchable text ────────────────────────────────────

DAY = 86400.0


@pytest.fixture
def period(tmp_path):
    data = tmp_path / "app"
    media = data / "statics" / "media"
    old, new = media / "old.jpg", media / "new.jpg"
    write(old, 11)
    write(new, 22)
    now = time.time()
    os.utime(old, (now - 10 * DAY, now - 10 * DAY))
    return fm.FileManager(data, cache_ttl=0), now, new


def _rels(page):
    return sorted(r["rel"] for r in page["items"])


def test_period_bounds(period):
    m, now, new = period
    assert _rels(m.list_entries("received")) == ["new.jpg", "old.jpg"]
    assert _rels(m.list_entries("received", date_from=now - 2 * DAY)) == ["new.jpg"]
    assert _rels(m.list_entries("received", date_to=now - 2 * DAY)) == ["old.jpg"]
    assert _rels(m.list_entries("received", date_from=new.stat().st_mtime)) == ["new.jpg"]  # inclusive
    assert _rels(m.list_entries("received", date_to=new.stat().st_mtime)) == ["old.jpg"]    # exclusive
    assert _rels(m.list_entries("received", date_from=now - 11 * DAY, date_to=now - 9 * DAY)) == ["old.jpg"]
    assert m.list_entries("received", date_from=now + DAY)["total"] == 0
    assert m.list_entries("received", date_from=now - 2 * DAY)["total_bytes"] == 22


def test_selector_filters_and_decorates(period):
    m, _now, _new = period
    picked: list[str] = []

    def pick_new(entry, db_path):
        picked.append(db_path)
        return {"match": "name"} if entry.rel.endswith("new.jpg") else None

    sel = m.list_entries("received", selector=pick_new)
    assert _rels(sel) == ["new.jpg"]
    assert "statics/media/new.jpg" in picked
    assert sel["items"][0]["match"] == "name"
    assert _rels(m.list_entries("received", selector=lambda e, p: {})) == ["new.jpg", "old.jpg"]


def test_searchable_text_ignores_the_placeholders_the_app_writes():
    for t in ("[Áudio recebido]", "[Imagem enviada]", "[Imagem enviada pelo contato]",
              "[Vídeo recebido]", "[Sticker]", "[Documento recebido: Contrato.pdf]",
              "[Documento enviado: documento]", "[Áudio]", "[Imagem]", "[Vídeo]"):
        assert fm.searchable_text(t) == "", t
    assert fm.searchable_text("obra da rua A") == "obra da rua A"
    assert fm.searchable_text("[Transcrição do áudio]: preciso do boleto") == "preciso do boleto"
    assert fm.searchable_text("[Descrição da imagem]: um recibo\n[Imagem recebida]") == "um recibo"
    assert fm.searchable_text(
        "[Documento recebido: a.pdf]\n[Conteúdo do documento]: cláusula quinta") == "cláusula quinta"
    assert fm.searchable_text("") == "" and fm.searchable_text(None) == ""
    assert fm.searchable_text("pedido [urgente] hoje") == "pedido [urgente] hoje"


def test_searchable_text_drops_the_group_author_prefix():
    assert fm.searchable_text("[Fulano de Tal]: [Imagem recebida]") == ""
    assert fm.searchable_text("[Fulano]: [Descrição da imagem]: um recibo") == "um recibo"
    assert fm.searchable_text("[Fulano]: obra da rua A") == "obra da rua A"
