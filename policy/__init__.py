"""Policy, roles and security for INA v2 (Member 2).

Entry points:
    PolicyGuard                      token -> role -> engine -> audit (MCP server)
    PolicyEngine                     pure allow/deny decisions with reasons
    integration.evaluate_policy      drop-in for network/service.py's placeholder

Names are imported lazily so that ``python -m policy.audit`` and
``python -m policy.authorization`` run without import warnings.
"""

from importlib import import_module

_EXPORTS = {
    "AuditError": "audit", "AuditLog": "audit",
    "AuthConfigError": "authorization", "Principal": "authorization",
    "TokenAuthenticator": "authorization",
    "PolicyConfigError": "config",
    "Evaluation": "engine", "PolicyEngine": "engine",
    "GuardResult": "guard", "PolicyGuard": "guard",
    "evaluate_policy": "integration", "evaluate_policy_decision": "integration",
    "get_tools_for_role": "integration",
    "Device": "models", "PolicyDecision": "models",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    if name in _EXPORTS:
        return getattr(import_module(f".{_EXPORTS[name]}", __name__), name)
    raise AttributeError(f"module 'policy' has no attribute {name!r}")
