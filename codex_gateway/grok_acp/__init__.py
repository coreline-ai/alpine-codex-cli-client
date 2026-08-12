"""Pinned Grok ACP backend internals.

Nothing in this package accepts an executable, environment, profile, or protocol method from an
Android request. Those values are fixed by :mod:`codex_gateway.grok_acp.policy`.
"""

from .policy import GrokLaunchPolicy, GrokPolicyError

__all__ = ["GrokLaunchPolicy", "GrokPolicyError"]
