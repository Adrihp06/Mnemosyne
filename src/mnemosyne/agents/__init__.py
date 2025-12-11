"""
Agent implementation for duplicate detection using Claude Agent SDK.
"""

from mnemosyne.agents.react import ReactAgent, detect_duplicates, get_agent

__all__ = [
    "ReactAgent",
    "get_agent",
    "detect_duplicates",
]
