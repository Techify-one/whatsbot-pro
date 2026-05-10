"""Core LLM tool definitions for the AgentHandler.

Each tool is defined in its own module and exposes:

- a schema dict (``{"type": "function", "function": {...}}``) named in
  ``UPPER_SNAKE_CASE`` ending with ``_TOOL``
- an ``execute(ctx, args)`` function that receives a ``plugins.context.ToolContext``
  and the parsed JSON args, and returns either ``None`` (default follow-up
  feedback) or a string used as the ``tool`` message in the follow-up call.

To add a new core tool, create a file here, implement schema + ``execute``,
and append the pair to ``CORE_TOOLS``.

For plugin tools, see ``plugins/loader.py``.
"""

from agent.tools.save_contact_info import (
    SAVE_CONTACT_INFO_TOOL,
    execute as _exec_save_contact_info,
)
from agent.tools.transfer_to_human import (
    TRANSFER_TO_HUMAN_TOOL,
    execute as _exec_transfer_to_human,
)

# (schema, executor) tuples — registered by AgentHandler at construction time.
CORE_TOOLS: list[tuple[dict, callable]] = [
    (SAVE_CONTACT_INFO_TOOL, _exec_save_contact_info),
    (TRANSFER_TO_HUMAN_TOOL, _exec_transfer_to_human),
]

# Backward-compatible flat list of schemas (some logging/track_step code reads it).
ALL_TOOLS: list[dict] = [schema for schema, _ in CORE_TOOLS]
