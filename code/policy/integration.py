"""Drop-in adapters for the service layer (network/service.py) and MCP server.

``network/service.py`` currently calls a temporary
``evaluate_policy_placeholder(action, client_ip, target, role) -> (bool, str)``
and a hard-coded ``_check_role``. This module offers the same call shape, backed
by the real policy engine, so the owner of the service layer can switch over by
changing one import:

    from inav2.policy.integration import evaluate_policy

    permitted, reason = evaluate_policy(
        action="ban_client", client=args.client, client_ip=station.ip,
        client_mac=station.mac, role=role, duration_minutes=minutes,
    )
    if not permitted:
        self._audit_denied(role, "ban_client", args.client, reason)
        raise PolicyDeniedError(reason)

Use ``evaluate_policy_decision`` to also get the rule ID (for audit/results).

The engine is loaded once (lazily) from the YAML files next to this module.
If the policy files are missing or invalid, every request is denied.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

from .config import PolicyConfigError
from .engine import PolicyEngine
from .models import R_INTERNAL, PolicyDecision, deny

_MISSING = object()
_engine: Optional[PolicyEngine] = None
_lock = threading.Lock()


def get_engine(reload: bool = False) -> PolicyEngine:
    """The shared engine, loaded from policy/*.yaml on first use."""
    global _engine
    with _lock:
        if _engine is None or reload:
            _engine = PolicyEngine.from_files()
        return _engine


def evaluate_policy_decision(
    action: str,
    client_ip: str = "",
    target: Optional[str] = None,
    role: str = "operator",
    *,
    client: Optional[str] = None,
    client_mac: Optional[str] = None,
    target_ip: Optional[str] = None,
    target_mac: Optional[str] = None,
    download_kbit: Optional[int] = None,
    upload_kbit: Optional[int] = None,
    duration_minutes: Any = _MISSING,
    reason: Optional[str] = None,
    confirm: Optional[bool] = None,
    banned_by_role: Any = _MISSING,
    engine: Optional[PolicyEngine] = None,
) -> PolicyDecision:
    """Evaluate one service-layer action and return the full decision.

    ``client`` is the identifier the caller gave (name, IP or MAC); if omitted,
    ``client_ip`` is used. ``client_ip``/``client_mac`` are what the service
    resolved it to and are cross-checked against the device list.
    ``banned_by_role`` must be passed for unban_client (None if not banned).
    """
    try:
        eng = engine or get_engine()
    except PolicyConfigError as exc:
        return deny(R_INTERNAL, f"Policy configuration is invalid; request denied ({exc}).")

    arguments: Dict[str, Any] = {}
    ident = client or client_ip or client_mac
    if ident:
        arguments["client"] = ident
    if target is not None:
        arguments["target"] = target
    if download_kbit is not None:
        arguments["download_kbit"] = download_kbit
    if upload_kbit is not None:
        arguments["upload_kbit"] = upload_kbit
    if duration_minutes is not _MISSING:
        arguments["duration_minutes"] = duration_minutes
    if reason is not None:
        arguments["reason"] = reason
    if confirm is not None:
        arguments["confirm"] = confirm

    context: Dict[str, Any] = {}
    for key, value in (("client_ip", client_ip), ("client_mac", client_mac),
                       ("target_ip", target_ip), ("target_mac", target_mac)):
        if value:
            context[key] = value
    if banned_by_role is not _MISSING:
        context["banned_by_role"] = banned_by_role

    try:
        return eng.evaluate(role, action, arguments, context).decision
    except Exception:  # noqa: BLE001 - fail closed
        return deny(R_INTERNAL, "The policy check failed; request denied.")


def evaluate_policy(action: str, client_ip: str = "", target: Optional[str] = None,
                    role: str = "operator", **kwargs: Any) -> Tuple[bool, str]:
    """Same signature and return shape as ``evaluate_policy_placeholder``."""
    decision = evaluate_policy_decision(action, client_ip, target, role, **kwargs)
    return decision.allowed, decision.reason


def get_tools_for_role(role: str = "operator") -> List[str]:
    """Policy-backed replacement for ``mcp_server.server.get_tools_for_role``."""
    try:
        return get_engine().tools_for_role(role.strip().lower())
    except (PolicyConfigError, AttributeError):
        return []
