"""``ai_engine`` — the portable, config-in-DB engine layer (plano 06).

Pure, agno-free helpers extracted from ``agent/agno_engine.py`` so they can be
unit-tested and reused. Today: ``model_factory`` (multi-provider model params +
tuning cascade) and ``dynamic_registry`` (cached config-in-DB reads). The agno
runner in ``agent/agno_engine.py`` consumes these.
"""
