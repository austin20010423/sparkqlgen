"""Backward-compat shim. The agent now builds the system prompt per-turn from
modular skill fragments — see `skills.py`. This module re-exports the core
prompt under the legacy `SYSTEM_PROMPT` name for any external caller that
hasn't migrated yet.
"""

from .skills import CORE_PROMPT as SYSTEM_PROMPT  # noqa: F401
