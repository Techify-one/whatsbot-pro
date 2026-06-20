"""Core runtime foundation (plano 09).

Cross-cutting runtime capabilities consumed by the core and by plugins:

- :mod:`runtime.supervisor` — background task supervisor (registry + classified
  restart + backoff), generalizing the previously-hardcoded lifespan tasks.

Future phases add a managed subprocess service here.
"""

from runtime.supervisor import RestartPolicy, TaskSpec, TaskSupervisor

__all__ = ["RestartPolicy", "TaskSpec", "TaskSupervisor"]
