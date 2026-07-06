"""ai_agents JSON columns são JSONB nativo (plano 34 F5 · Track B).

Fecha na ORIGEM a classe de dupla-codificação do apagão de resolução de agente:
``model_config``/``tool_names``/``routing_targets``/``hooks_config`` viram JSONB
nativo (migration 0039). O repo passa dict/list nativo e o SQLAlchemy serializa 1×
— nunca ``json.dumps`` à mão —, então é impossível gravar uma string JSON crua na
coluna. A leitura mantém ``coerce_json`` como no-op defensivo.

Cobre:
- 1) pg_typeof das 4 colunas = jsonb; jsonb_typeof(model_config) = object.
- 2) round-trip: save(dict/list) → get devolve o MESMO objeto nativo.
- 3) defesa: um caller sujo que passa uma STRING JSON não produz string JSONB
     (``coerce_json`` desembrulha antes de chegar ao banco).
- 4) migration robusta: downgrade→0038 (TEXT), semeia linhas SUJAS (limpa, vazia,
     NULL, DUPLO-codificada), upgrade→0039 e confere a conversão de cada uma.
- 5) up/down round-trip do Alembic mantém a coluna jsonb ao fim.

    venv/bin/python tests/test_ai_agents_jsonb.py
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.pg import init_test_engine  # noqa: E402
init_test_engine(reset=True)

from sqlalchemy import text  # noqa: E402
from db.engine import get_engine  # noqa: E402
from db.repositories import agent_repo  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, cond: bool):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  OK {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


def _col_type(col: str) -> str:
    with get_engine().connect() as conn:
        return conn.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='ai_agents' AND column_name=:c"), {"c": col}).scalar()


def _alembic_cfg():
    from alembic.config import Config
    root = PROJECT_ROOT
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "db" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(get_engine().url).replace("%", "%%"))
    return cfg


# --------------------------------------------------------------------------- #
# 1) tipos: as 4 colunas são jsonb; model_config guarda um object, não string
# --------------------------------------------------------------------------- #
print("1) tipos das colunas (jsonb) + object storage:")
for col in ("model_config", "tool_names", "routing_targets", "hooks_config"):
    check(f"{col} é jsonb no schema", _col_type(col) == "jsonb")

agent_repo.save(
    "default", display_name="D", prompt="p",
    model_config={"model": "x/y", "temperature": 0.5},
    tool_names=["a", "b"], enabled=True,
    is_router=False, routing_targets=["default"], hooks_config={"h": 1},
)
with get_engine().connect() as conn:
    kinds = conn.execute(text(
        "SELECT jsonb_typeof(model_config) mc, jsonb_typeof(tool_names) tn, "
        "jsonb_typeof(routing_targets) rt, jsonb_typeof(hooks_config) hc "
        "FROM ai_agents WHERE agent_key='default'")).mappings().first()
check("jsonb_typeof(model_config)=object (não string)", kinds["mc"] == "object")
check("jsonb_typeof(tool_names)=array", kinds["tn"] == "array")
check("jsonb_typeof(routing_targets)=array", kinds["rt"] == "array")
check("jsonb_typeof(hooks_config)=object", kinds["hc"] == "object")

# --------------------------------------------------------------------------- #
# 2) round-trip: save(dict/list) → get devolve o mesmo objeto nativo
# --------------------------------------------------------------------------- #
print("2) round-trip nativo:")
got = agent_repo.get("default")
check("model_config round-trip == dict original", got["model_config"] == {"model": "x/y", "temperature": 0.5})
check("model_config é dict nativo (não str)", isinstance(got["model_config"], dict))
check("tool_names round-trip == list", got["tool_names"] == ["a", "b"])
check("routing_targets round-trip == list", got["routing_targets"] == ["default"])
check("hooks_config round-trip == dict", got["hooks_config"] == {"h": 1})

# --------------------------------------------------------------------------- #
# 3) defesa: caller sujo passa STRING JSON → não vira string JSONB
# --------------------------------------------------------------------------- #
print("3) string JSON crua é normalizada (não dupla-codifica):")
agent_repo.save(
    "default", display_name="D", prompt="p",
    model_config=json.dumps({"model": "z/w"}),      # <- STRING, não dict
    tool_names=None, enabled=True,
)
got = agent_repo.get("default")
check("model_config de string crua → objeto", got["model_config"] == {"model": "z/w"})
with get_engine().connect() as conn:
    t = conn.execute(text(
        "SELECT jsonb_typeof(model_config) FROM ai_agents WHERE agent_key='default'")).scalar()
check("após string crua: jsonb ainda é object", t == "object")

# --------------------------------------------------------------------------- #
# 4) migration robusta: downgrade→0038 (TEXT), semeia sujo, upgrade→0039
# --------------------------------------------------------------------------- #
print("4) migration 0039 USING robusto (dados sujos):")
from alembic import command  # noqa: E402
cfg = _alembic_cfg()
command.downgrade(cfg, "0038_channels_account_identity")
check("após downgrade: model_config é text", _col_type("model_config") == "text")

double = json.dumps(json.dumps({"model": "x/y"}))   # string JSON dentro de string JSON
cols = ("agent_key", "display_name", "prompt", "prompt_key", "model_config",
        "tool_names", "enabled", "description", "is_router", "routing_targets",
        "hooks_config", "version", "updated_at")
ins = ("INSERT INTO ai_agents (" + ", ".join(cols) + ") VALUES "
       "(:agent_key, :display_name, :prompt, :prompt_key, :model_config, "
       ":tool_names, :enabled, :description, :is_router, :routing_targets, "
       ":hooks_config, :version, :updated_at)")
with get_engine().begin() as conn:
    conn.execute(text("DELETE FROM ai_agents"))
    # linha LIMPA: JSON válido nas 4 colunas
    conn.execute(text(ins), {
        "agent_key": "clean", "display_name": "C", "prompt": "", "prompt_key": "",
        "model_config": '{"model": "a/b"}', "tool_names": '["t1"]', "enabled": 1,
        "description": "", "is_router": 0, "routing_targets": '["clean"]',
        "hooks_config": "{}", "version": 1, "updated_at": 0,
    })
    # linha SUJA: model_config DUPLO-codificado, hooks_config vazio, tool_names/routing NULL
    conn.execute(text(ins), {
        "agent_key": "dirty", "display_name": "D", "prompt": "", "prompt_key": "",
        "model_config": double, "tool_names": None, "enabled": 1,
        "description": "", "is_router": 0, "routing_targets": None,
        "hooks_config": "", "version": 1, "updated_at": 0,
    })

command.upgrade(cfg, "head")
check("após upgrade: model_config voltou a jsonb", _col_type("model_config") == "jsonb")

with get_engine().connect() as conn:
    rows = {r["agent_key"]: r for r in conn.execute(text(
        "SELECT agent_key, model_config, tool_names, routing_targets, hooks_config, "
        "jsonb_typeof(model_config) mct FROM ai_agents")).mappings()}
# linha limpa converteu certo
check("clean: model_config → objeto", rows["clean"]["model_config"] == {"model": "a/b"})
check("clean: tool_names → array", rows["clean"]["tool_names"] == ["t1"])
check("clean: routing_targets → array", rows["clean"]["routing_targets"] == ["clean"])
# linha suja: duplo-codificado foi DESEMBRULHADO para objeto real
check("dirty: model_config jsonb_typeof=object (unwrap do duplo)", rows["dirty"]["mct"] == "object")
check("dirty: model_config == objeto real", rows["dirty"]["model_config"] == {"model": "x/y"})
check("dirty: hooks_config vazio → {}", rows["dirty"]["hooks_config"] == {})
check("dirty: tool_names NULL → None", rows["dirty"]["tool_names"] is None)
check("dirty: routing_targets NULL → None", rows["dirty"]["routing_targets"] is None)

# --------------------------------------------------------------------------- #
# 5) o repo continua funcionando após o round-trip de migration
# --------------------------------------------------------------------------- #
print("5) repo funcional pós up/down:")
agent_repo.save("default", display_name="D2", prompt="p2",
                model_config={"model": "final"}, tool_names=["z"], enabled=True)
again = agent_repo.get("default")
check("save/get pós-migration ok", again["model_config"] == {"model": "final"} and again["tool_names"] == ["z"])

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
