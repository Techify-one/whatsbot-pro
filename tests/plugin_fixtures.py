"""Pytest fixtures exported for the external WhatsBot plugins repository.

The production application never imports this module.  The sibling plugins
repository registers it explicitly while running development tests, allowing
plugin suites to reuse the same PostgreSQL bootstrap and hermetic app builder
as the core without copying fixture implementations.
"""

from tests.conftest import (  # noqa: F401
    _engine_ready,
    app,
    authenticated_admin,
    build_app,
    client,
    mock_gowa_client,
    mock_gowa_manager,
    plugin_app,
    seed,
)


__all__ = [
    "_engine_ready",
    "app",
    "authenticated_admin",
    "build_app",
    "client",
    "mock_gowa_client",
    "mock_gowa_manager",
    "plugin_app",
    "seed",
]
