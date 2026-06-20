"""Core runtime foundation (plano 09).

Cross-cutting runtime capabilities consumed by the core and by plugins:

- :mod:`runtime.supervisor` — background task supervisor (registry + classified
  restart + backoff), generalizing the previously-hardcoded lifespan tasks.
- :mod:`runtime.subprocess_service` — managed subprocess service (process group,
  die-with-parent, stale-kill, readiness, watchdog), used to harden the GOWA bridge.
"""

from runtime.subprocess_service import (
    ManagedProcess,
    OneShotResult,
    SubprocessService,
    SubprocessSpec,
    run_oneshot,
)
from runtime.supervisor import RestartPolicy, TaskSpec, TaskSupervisor

__all__ = [
    "RestartPolicy", "TaskSpec", "TaskSupervisor",
    "ManagedProcess", "SubprocessService", "SubprocessSpec",
    "OneShotResult", "run_oneshot",
]
