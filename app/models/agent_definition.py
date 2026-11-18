"""
Agent definitions (design/agent-overlay.md).

What a plugin hands open core for one packaged agent, already in Ziya's
terms: resolved prompt text, absolute context file paths, names of skills,
servers and subagents.  Vendor formats and template tokens stay in the
plugin.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

# How one part of a definition was imported:
#   applied      used as written
#   mapped       translated to a Ziya equivalent
#   unavailable  needs something that is not here (``items`` names it)
#   unused       not used, with the reason in ``does``
#   startup      a command run when the agent is turned on
#   pending      understood, but this build does not act on it yet
IMPORT_OUTCOMES = ("applied", "mapped", "unavailable", "unused", "startup", "pending")


class ImportRow(BaseModel):
    """One line of the import view: what the source said, what Ziya does."""
    part: str
    says: str = ""
    does: str = ""
    outcome: str = "applied"
    items: List[str] = []


class AgentRules(BaseModel):
    shell_allow: List[str] = []
    shell_deny: List[str] = []
    read_deny: List[str] = []
    write_allow: List[str] = []
    mcp_allow: List[str] = []

    def restricts(self) -> bool:
        """True when the definition denies something.  Only deny lists
        take away from Ziya's own policy; allow lists add to it and never
        remove anything."""
        return bool(self.shell_deny or self.read_deny)

    def any(self) -> bool:
        return bool(self.shell_allow or self.shell_deny or self.read_deny
                    or self.write_allow or self.mcp_allow)


class StartupCommand(BaseModel):
    command: str
    timeout_secs: Optional[float] = None


class AgentDefinition(BaseModel):
    name: str
    description: str = ""
    # The package it was installed with, and any other package shipping an
    # agent of the same name (the first one listed is the one used).
    package: str = ""
    also_in: List[str] = []
    # Where the package is installed.  Shell allow rules for scripts under
    # this directory are granted to the conversation while the agent is on
    # (app/services/agent_enforcement.shell_grants_for); None grants none.
    install_root: Optional[str] = None
    prompt: str = ""
    context_files: List[str] = []
    # Agent SOPs (``<name>.sop.md``, app/utils/agent_sops.py) loaded in full
    # while the agent is on, by name.
    sops: List[str] = []
    model: Optional[str] = None
    skills: List[str] = []
    servers: List[str] = []
    # None: the definition does not limit tools.
    tools: Optional[List[str]] = None
    rules: AgentRules = AgentRules()
    subagents: List[str] = []
    startup: List[StartupCommand] = []
    # Extra import rows only the plugin can explain (parts of the source
    # format with no counterpart here).
    notes: List[ImportRow] = []
