"""LLM tool definitions for the AgentHandler.

Each tool is defined in its own module and exported here.
To add a new tool, create a file in this folder and add it to ALL_TOOLS.
"""

from agent.tools.save_contact_info import SAVE_CONTACT_INFO_TOOL

ALL_TOOLS: list[dict] = [
    SAVE_CONTACT_INFO_TOOL,
]
