"""LLM tool definitions for the AgentHandler.

Each tool is defined in its own module and exported here.
To add a new tool, create a file in this folder and add it to ALL_TOOLS.
"""

from agent.tools.save_contact_info import SAVE_CONTACT_INFO_TOOL
from agent.tools.transfer_to_human import TRANSFER_TO_HUMAN_TOOL

ALL_TOOLS: list[dict] = [
    SAVE_CONTACT_INFO_TOOL,
    TRANSFER_TO_HUMAN_TOOL,
]
